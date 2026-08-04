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
from app.ingesters.classification import classify_run
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


def _is_fatal_persist_error(persist_result) -> bool:
    """True if the whole batch failed (DB-level error), not just per-signal rejects.

    persist_environmental_observations() never raises — a fatal failure (e.g. the
    upsert statement itself erroring, or the transaction failing to commit) is only
    signalled via a "persist_error: ..." entry in PersistResult.errors, distinct
    from the per-signal "signal_<idx>: ..." validation-rejection entries.
    """
    return any(e.startswith("persist_error:") for e in persist_result.errors)


def _accepted_indicator_counts(signals, persist_result) -> Dict[str, int]:
    """Map indicator -> count of signals actually persisted (accepted), for
    Prometheus observation counters that must reflect accepted records, not
    the raw fetch count.

    Rejected signal indices are derived from the "signal_<idx>: ..." entries
    persist_environmental_observations() appends to PersistResult.errors for
    per-signal validation failures (missing region_code/metric/value). Must
    only be called after confirming _is_fatal_persist_error() is False —
    on a fatal batch failure nothing was actually persisted.
    """
    if _is_fatal_persist_error(persist_result):
        return {}

    rejected_indices = set()
    for err in persist_result.errors:
        if err.startswith("signal_"):
            try:
                rejected_indices.add(int(err.split("_", 1)[1].split(":", 1)[0]))
            except (IndexError, ValueError):
                continue

    counts: Dict[str, int] = {}
    for idx, signal in enumerate(signals):
        if idx in rejected_indices:
            continue
        counts[signal.metric] = counts.get(signal.metric, 0) + 1
    return counts


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
        # Warning for anything short of success. A degraded run recorded at
        # info level disappears wherever info is filtered -- which is most
        # places -- leaving exactly the invisibility this change removes from
        # the database while keeping it in the logs.
        emit = logger.info if status == "success" else logger.warning
        emit(
            "Job %s: %s (processed=%d, rejected=%d, duration=%.2fs)%s",
            job_name, status, records_processed, records_rejected,
            duration_sec or 0,
            " -- %s" % error_message if error_message and status != "success" else "",
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
    from app.ingesters.persist import persist_environmental_observations
    from app.prom_metrics import (
        sora_environmental_ingestion_total,
        sora_environmental_ingestion_errors_total,
        sora_environmental_source_freshness_seconds,
        sora_environmental_observations_total,
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
        fetched_count = len(signals)

        # Fetch-time quality flag: informational only. NOT the authoritative
        # persisted-rejection count — that comes from PersistResult below.
        quality_valid_signals = [s for s in signals if s.metadata.get("quality") != "invalid"]
        quality_invalid_count = fetched_count - len(quality_valid_signals)

        # Persist signals to environmental_observations (upsert on source, source_record_id).
        # This is the single source of truth for accepted/rejected counts; success is
        # only declared below once this has actually completed without a fatal error.
        persist_result = persist_environmental_observations(signals, "openaq")

        if _is_fatal_persist_error(persist_result):
            raise RuntimeError(f"persistence failed for openaq: {persist_result.errors}")

        # What the run achieved, not whether it threw. This job recorded
        # "success" on every non-exception path, which is how sixty-two openaq
        # runs came to report success having written nothing: the source is
        # reachable, authenticated and empty (#56, #57).
        verdict = classify_run(
            received=persist_result.received,
            accepted=persist_result.accepted,
            rejected=persist_result.rejected,
            # No write_failed here, deliberately. _is_fatal_persist_error()
            # above already raises on a "persist_error:" entry, so this line is
            # unreachable with one present -- I added it anyway, following a
            # review finding that was correct for runner.py, which has no such
            # guard, without reading what stood four lines higher. Dead code
            # that looks load-bearing is worse than none.
        )

        # Update Prometheus metrics only after persistence has succeeded, counting
        # accepted (persisted) records rather than fetched ones. The label carries
        # the verdict, so a degraded run stops being indistinguishable from a
        # working one on the dashboard as well as in the table.
        sora_environmental_ingestion_total.labels(
            source="openaq", status=verdict.status
        ).inc()
        for indicator, count in _accepted_indicator_counts(signals, persist_result).items():
            sora_environmental_observations_total.labels(
                source="openaq", indicator=indicator
            ).inc(count)

        # Calculate data freshness (time since most recent observation)
        # Guarded on the timestamps, not on the signals.
        #
        # `if signals:` is not the condition this needs: the generator filters to
        # signals whose observed_at is a datetime, and a batch where none is --
        # a source that omitted the field, or returned it unparseable -- leaves
        # max() with an empty sequence and raises ValueError, failing a run that
        # had otherwise succeeded. Found by a test that passed a signal without a
        # timestamp, which is a shape the real sources can produce.
        observed_times = [
            s.observed_at for s in signals
            if isinstance(s.observed_at, datetime)
        ]
        if observed_times:
            latest_time = max(observed_times)
            freshness_seconds = (datetime.now(timezone.utc) - latest_time).total_seconds()
            sora_environmental_source_freshness_seconds.labels(source="openaq").set(
                freshness_seconds
            )

        duration = time.time() - start_time

        # Log to database: records_processed/rejected reflect what was actually
        # persisted (PersistResult), never the fetch-time quality-flag count.
        _log_job_execution(
            job_name=job_name,
            status=verdict.status,
            duration_sec=duration,
            records_processed=persist_result.accepted,
            records_rejected=persist_result.rejected,
            error_message=verdict.failure_reason,
            metadata={
                "fetched_count": fetched_count,
                "quality_valid_count": len(quality_valid_signals),
                "quality_invalid_count": quality_invalid_count,
                "parameters": list(set(s.metric for s in signals)),
                "persist_inserted": persist_result.inserted,
                "persist_updated": persist_result.updated,
                "persist_duplicates": persist_result.duplicates,
                "persist_errors": persist_result.errors[:5] if persist_result.errors else [],
            }
        )

        logger.info(
            "OpenAQ ingestion completed: fetched %d signals, persisted %d accepted / "
            "%d rejected (%d inserted, %d updated) in %.2fs",
            fetched_count, persist_result.accepted, persist_result.rejected,
            persist_result.inserted, persist_result.updated, duration
        )

        # The verdict, not a constant. Returning "success" beside a row
        # recorded as degraded gives the same run two different answers
        # depending on who asks, which is how the two drift apart.
        return {
            "status": verdict.status,
            "failure_reason": verdict.failure_reason,
            "signals_count": fetched_count,
            "valid_count": len(quality_valid_signals),
            "rejected_count": quality_invalid_count,
            "persisted": persist_result.accepted,
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
    from app.ingesters.persist import persist_environmental_observations
    from app.prom_metrics import (
        sora_environmental_ingestion_total,
        sora_environmental_ingestion_errors_total,
        sora_environmental_source_freshness_seconds,
        sora_environmental_observations_total,
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
        fetched_count = len(signals)

        # Persist signals to environmental_observations (upsert on source, source_record_id).
        # Single source of truth for accepted/rejected counts; success is only
        # declared below once this has actually completed without a fatal error.
        persist_result = persist_environmental_observations(signals, "openmeteo")

        if _is_fatal_persist_error(persist_result):
            raise RuntimeError(f"persistence failed for openmeteo: {persist_result.errors}")

        # Update Prometheus metrics only after persistence has succeeded, counting
        # accepted (persisted) records rather than fetched ones.
        verdict = classify_run(
            received=persist_result.received,
            accepted=persist_result.accepted,
            rejected=persist_result.rejected,
            # No write_failed here, deliberately. _is_fatal_persist_error()
            # above already raises on a "persist_error:" entry, so this line is
            # unreachable with one present -- I added it anyway, following a
            # review finding that was correct for runner.py, which has no such
            # guard, without reading what stood four lines higher. Dead code
            # that looks load-bearing is worse than none.
        )
        sora_environmental_ingestion_total.labels(
            source="openmeteo", status=verdict.status
        ).inc()
        for indicator, count in _accepted_indicator_counts(signals, persist_result).items():
            sora_environmental_observations_total.labels(
                source="openmeteo", indicator=indicator
            ).inc(count)

        # Calculate data freshness
        # Guarded on the timestamps, not on the signals.
        #
        # `if signals:` is not the condition this needs: the generator filters to
        # signals whose observed_at is a datetime, and a batch where none is --
        # a source that omitted the field, or returned it unparseable -- leaves
        # max() with an empty sequence and raises ValueError, failing a run that
        # had otherwise succeeded. Found by a test that passed a signal without a
        # timestamp, which is a shape the real sources can produce.
        observed_times = [
            s.observed_at for s in signals
            if isinstance(s.observed_at, datetime)
        ]
        if observed_times:
            latest_time = max(observed_times)
            freshness_seconds = (datetime.now(timezone.utc) - latest_time).total_seconds()
            sora_environmental_source_freshness_seconds.labels(source="openmeteo").set(
                freshness_seconds
            )

        duration = time.time() - start_time

        # Log to database: records_processed/rejected reflect what was actually
        # persisted (PersistResult), never the raw fetch count.
        _log_job_execution(
            job_name=job_name,
            status=verdict.status,
            duration_sec=duration,
            records_processed=persist_result.accepted,
            records_rejected=persist_result.rejected,
            error_message=verdict.failure_reason,
            metadata={
                "fetched_count": fetched_count,
                "regions_count": len(set(s.region_code for s in signals)),
                "variables": list(set(s.metric for s in signals)),
                "persist_inserted": persist_result.inserted,
                "persist_updated": persist_result.updated,
                "persist_duplicates": persist_result.duplicates,
                "persist_errors": persist_result.errors[:5] if persist_result.errors else [],
            }
        )

        logger.info(
            "Open-Meteo ingestion completed: fetched %d signals, persisted %d accepted / "
            "%d rejected (%d inserted, %d updated) in %.2fs",
            fetched_count, persist_result.accepted, persist_result.rejected,
            persist_result.inserted, persist_result.updated, duration
        )

        # The verdict, not a constant. Returning "success" beside a row
        # recorded as degraded gives the same run two different answers
        # depending on who asks, which is how the two drift apart.
        return {
            "status": verdict.status,
            "failure_reason": verdict.failure_reason,
            "signals_count": fetched_count,
            "persisted": persist_result.accepted,
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
