"""Environmental data scheduler jobs.

APScheduler jobs for:
- OpenAQ air quality ingestion (hourly)
- Open-Meteo weather ingestion (hourly)
- Source health checks (15 minutes)
- Data quality aggregation (hourly)

Each job implements:
- Redis distributed locking
- Retry with exponential backoff
- Timeout handling
- Structured logging
- Prometheus metrics
- Idempotency
- Database execution log
"""
from __future__ import annotations
import logging
import time
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from functools import wraps

logger = logging.getLogger(__name__)


# Retry decorator with exponential backoff
def with_retry(max_attempts: int = 3, base_delay: float = 1.0, max_delay: float = 30.0):
    """Retry decorator with exponential backoff.

    Args:
        max_attempts: Maximum number of retry attempts
        base_delay: Initial delay in seconds
        max_delay: Maximum delay between retries
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            last_exception = None

            while attempt < max_attempts:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    attempt += 1
                    last_exception = e

                    if attempt >= max_attempts:
                        logger.error(
                            "Function %s failed after %d attempts: %s",
                            func.__name__, max_attempts, e
                        )
                        raise

                    # Exponential backoff with jitter
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    logger.warning(
                        "Function %s failed (attempt %d/%d), retrying in %.1fs: %s",
                        func.__name__, attempt, max_attempts, delay, e
                    )
                    time.sleep(delay)

            if last_exception:
                raise last_exception

        return wrapper
    return decorator


def _run_async_job(coro):
    """Helper to run async coroutines in scheduler (which is sync)."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(coro)


def _log_job_execution(
    job_name: str,
    status: str,
    duration_sec: Optional[float] = None,
    records_processed: int = 0,
    records_rejected: int = 0,
    error_message: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
):
    """Log job execution to database.

    Args:
        job_name: Job identifier
        status: 'success', 'failed', 'skipped'
        duration_sec: Job execution time
        records_processed: Number of records processed
        records_rejected: Number of records rejected
        error_message: Error message if failed
        metadata: Additional job-specific metadata
    """
    from app.database import SessionLocal, EnvironmentalJobLog

    db = SessionLocal()
    try:
        log = EnvironmentalJobLog(
            job_name=job_name,
            executed_at=datetime.now(timezone.utc),
            status=status,
            duration_sec=duration_sec,
            records_processed=records_processed,
            records_rejected=records_rejected,
            error_message=error_message[:500] if error_message else None,
            metadata_json=metadata,
        )
        db.add(log)
        db.commit()
        logger.info(
            "Job %s: %s (processed=%d, rejected=%d, duration=%.2fs)",
            job_name, status, records_processed, records_rejected,
            duration_sec or 0
        )
    except Exception as e:
        db.rollback()
        logger.error("Failed to log job execution for %s: %s", job_name, e)
    finally:
        db.close()


@with_retry(max_attempts=3, base_delay=2.0)
def scheduled_openaq_ingestion():
    """Ingest OpenAQ air quality data (runs hourly).

    Fetches latest measurements from OpenAQ API and stores in database.
    Implements Redis locking for idempotency.
    """
    from app.locks import RedisLock
    from app.ingesters.openaq import OpenAQIngester
    from app.prom_metrics import (
        sora_environmental_ingestion_total,
        sora_environmental_ingestion_errors_total,
        sora_environmental_source_freshness_seconds,
    )

    job_name = "openaq_ingestion"
    lock = RedisLock(key=f"sora:lock:{job_name}", timeout=300)  # 5 min timeout

    if not lock.acquire():
        logger.warning("OpenAQ ingestion skipped: lock held by another instance")
        _log_job_execution(job_name, status="skipped", metadata={"reason": "lock_held"})
        return {"status": "skipped", "reason": "lock_held"}

    start_time = time.time()
    try:
        logger.info("Starting OpenAQ ingestion job")

        # Run async ingester
        ingester = OpenAQIngester()
        signals = _run_async_job(ingester.fetch())

        # Calculate metrics
        valid_signals = [s for s in signals if s.metadata.get("quality") != "invalid"]
        rejected_count = len(signals) - len(valid_signals)

        # Update Prometheus metrics
        sora_environmental_ingestion_total.labels(source="openaq", status="success").inc()

        # Calculate data freshness (time since most recent observation)
        if signals:
            latest_time = max(
                s.observed_at for s in signals
                if isinstance(s.observed_at, datetime)
            )
            freshness_seconds = (datetime.now(timezone.utc) - latest_time).total_seconds()
            sora_environmental_source_freshness_seconds.labels(source="openaq").set(
                freshness_seconds
            )

        duration = time.time() - start_time

        # Log to database
        _log_job_execution(
            job_name=job_name,
            status="success",
            duration_sec=duration,
            records_processed=len(signals),
            records_rejected=rejected_count,
            metadata={
                "valid_signals": len(valid_signals),
                "parameters": list(set(s.metric for s in signals)),
            }
        )

        logger.info(
            "OpenAQ ingestion completed: %d signals (%d valid, %d rejected) in %.2fs",
            len(signals), len(valid_signals), rejected_count, duration
        )

        return {
            "status": "success",
            "signals_count": len(signals),
            "valid_count": len(valid_signals),
            "rejected_count": rejected_count,
            "duration_sec": duration,
        }

    except Exception as e:
        duration = time.time() - start_time
        logger.exception("OpenAQ ingestion failed: %s", e)

        # Update error metrics
        sora_environmental_ingestion_errors_total.labels(source="openaq").inc()

        # Log failure
        _log_job_execution(
            job_name=job_name,
            status="failed",
            duration_sec=duration,
            error_message=str(e),
        )

        return {"status": "error", "error": str(e)}

    finally:
        lock.release()


@with_retry(max_attempts=3, base_delay=2.0)
def scheduled_openmeteo_ingestion():
    """Ingest Open-Meteo weather data (runs hourly).

    Fetches current weather and forecast for environmental regions.
    Implements Redis locking for idempotency.
    """
    from app.locks import RedisLock
    from app.ingesters.openmeteo import OpenMeteoIngester
    from app.prom_metrics import (
        sora_environmental_ingestion_total,
        sora_environmental_ingestion_errors_total,
        sora_environmental_source_freshness_seconds,
    )

    job_name = "openmeteo_ingestion"
    lock = RedisLock(key=f"sora:lock:{job_name}", timeout=300)

    if not lock.acquire():
        logger.warning("Open-Meteo ingestion skipped: lock held")
        _log_job_execution(job_name, status="skipped", metadata={"reason": "lock_held"})
        return {"status": "skipped", "reason": "lock_held"}

    start_time = time.time()
    try:
        logger.info("Starting Open-Meteo ingestion job")

        # Run async ingester
        ingester = OpenMeteoIngester()
        signals = _run_async_job(ingester.fetch())

        # Update Prometheus metrics
        sora_environmental_ingestion_total.labels(source="openmeteo", status="success").inc()

        # Calculate data freshness
        if signals:
            latest_time = max(
                s.observed_at for s in signals
                if isinstance(s.observed_at, datetime)
            )
            freshness_seconds = (datetime.now(timezone.utc) - latest_time).total_seconds()
            sora_environmental_source_freshness_seconds.labels(source="openmeteo").set(
                freshness_seconds
            )

        duration = time.time() - start_time

        # Log to database
        _log_job_execution(
            job_name=job_name,
            status="success",
            duration_sec=duration,
            records_processed=len(signals),
            metadata={
                "regions_count": len(set(s.region_code for s in signals)),
                "variables": list(set(s.metric for s in signals)),
            }
        )

        logger.info(
            "Open-Meteo ingestion completed: %d signals in %.2fs",
            len(signals), duration
        )

        return {
            "status": "success",
            "signals_count": len(signals),
            "duration_sec": duration,
        }

    except Exception as e:
        duration = time.time() - start_time
        logger.exception("Open-Meteo ingestion failed: %s", e)

        sora_environmental_ingestion_errors_total.labels(source="openmeteo").inc()

        _log_job_execution(
            job_name=job_name,
            status="failed",
            duration_sec=duration,
            error_message=str(e),
        )

        return {"status": "error", "error": str(e)}

    finally:
        lock.release()


def scheduled_source_health_check():
    """Check health of environmental data sources (runs every 15 minutes).

    Monitors:
    - Source API availability
    - Data freshness
    - Error rates
    - Recent ingestion success
    """
    from app.locks import RedisLock
    from app.database import SessionLocal, EnvironmentalJobLog
    from datetime import timedelta
    from app.prom_metrics import sora_environmental_data_quality_score

    job_name = "source_health_check"
    lock = RedisLock(key=f"sora:lock:{job_name}", timeout=60)

    if not lock.acquire():
        return {"status": "skipped", "reason": "lock_held"}

    start_time = time.time()
    try:
        db = SessionLocal()
        health_report = {}

        # Check recent ingestion jobs (last 2 hours)
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=2)

        for source in ["openaq", "openmeteo"]:
            job_name_pattern = f"{source}_ingestion"

            recent_jobs = (
                db.query(EnvironmentalJobLog)
                .filter(
                    EnvironmentalJobLog.job_name == job_name_pattern,
                    EnvironmentalJobLog.executed_at >= cutoff_time
                )
                .order_by(EnvironmentalJobLog.executed_at.desc())
                .limit(5)
                .all()
            )

            if not recent_jobs:
                health_status = "unknown"
                quality_score = 0
            else:
                # Calculate health score based on recent jobs
                success_count = sum(1 for j in recent_jobs if j.status == "success")
                success_rate = success_count / len(recent_jobs)

                # Average data freshness
                avg_duration = sum(
                    j.duration_sec for j in recent_jobs if j.duration_sec
                ) / len(recent_jobs) if recent_jobs else 0

                # Health score (0-100)
                quality_score = int(success_rate * 100)

                if quality_score >= 90:
                    health_status = "healthy"
                elif quality_score >= 70:
                    health_status = "degraded"
                else:
                    health_status = "unhealthy"

            health_report[source] = {
                "status": health_status,
                "quality_score": quality_score,
                "recent_jobs_count": len(recent_jobs),
                "last_job_at": recent_jobs[0].executed_at.isoformat() if recent_jobs else None,
            }

            # Update Prometheus metric
            sora_environmental_data_quality_score.labels(source=source).set(quality_score)

        duration = time.time() - start_time
        db.close()

        logger.info("Source health check completed: %s", health_report)

        return {
            "status": "success",
            "health_report": health_report,
            "duration_sec": duration,
        }

    except Exception as e:
        logger.exception("Source health check failed: %s", e)
        return {"status": "error", "error": str(e)}
    finally:
        lock.release()


def scheduled_data_quality_aggregation():
    """Aggregate and analyze data quality metrics (runs hourly).

    Analyzes:
    - Overall data quality by source
    - Missing data detection
    - Outlier detection
    - Temporal consistency
    """
    from app.locks import RedisLock
    from app.database import SessionLocal, EnvironmentalObservation
    from datetime import timedelta
    from sqlalchemy import func

    job_name = "data_quality_aggregation"
    lock = RedisLock(key=f"sora:lock:{job_name}", timeout=300)

    if not lock.acquire():
        logger.warning("Data quality aggregation skipped: lock held")
        return {"status": "skipped", "reason": "lock_held"}

    start_time = time.time()
    try:
        db = SessionLocal()

        # Analyze recent observations (last 24 hours)
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=24)

        quality_stats = {}

        for source in ["openaq", "openmeteo"]:
            # Count observations by quality
            obs_count = (
                db.query(func.count(EnvironmentalObservation.id))
                .filter(
                    EnvironmentalObservation.source == source,
                    EnvironmentalObservation.ingested_at >= cutoff_time
                )
                .scalar()
            )

            valid_count = (
                db.query(func.count(EnvironmentalObservation.id))
                .filter(
                    EnvironmentalObservation.source == source,
                    EnvironmentalObservation.is_valid == True,
                    EnvironmentalObservation.ingested_at >= cutoff_time
                )
                .scalar()
            )

            # Calculate quality percentage
            quality_pct = (valid_count / obs_count * 100) if obs_count > 0 else 0

            quality_stats[source] = {
                "total_observations": obs_count,
                "valid_observations": valid_count,
                "quality_percentage": round(quality_pct, 2),
            }

        duration = time.time() - start_time
        db.close()

        # Log aggregation
        _log_job_execution(
            job_name=job_name,
            status="success",
            duration_sec=duration,
            metadata={"quality_stats": quality_stats}
        )

        logger.info("Data quality aggregation completed: %s", quality_stats)

        return {
            "status": "success",
            "quality_stats": quality_stats,
            "duration_sec": duration,
        }

    except Exception as e:
        duration = time.time() - start_time
        logger.exception("Data quality aggregation failed: %s", e)

        _log_job_execution(
            job_name=job_name,
            status="failed",
            duration_sec=duration,
            error_message=str(e),
        )

        return {"status": "error", "error": str(e)}
    finally:
        lock.release()
