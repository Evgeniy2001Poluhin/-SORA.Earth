import os
import logging
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone="UTC")
_retrain_log = []


def retrain_models():
    """Auto-retrain all ML models, invalidate cache, and persist retrain log to DB."""
    from app.locks import RedisLock
    from app.database import SessionLocal, RetrainLog

    lock = RedisLock(key="sora:lock:model_retrain", timeout=600)
    if not lock.acquire():
        logger.warning("Retrain skipped: another retrain is already running")
        return {"status": "skipped", "reason": "lock_held"}

    db = SessionLocal()
    started_at = datetime.utcnow()
    log_row = RetrainLog(
        started_at=started_at,
        status="running",
        trigger_source="scheduler",
        job_name="model_retrain",
    )
    db.add(log_row)
    db.commit()
    db.refresh(log_row)

    start_time = time.time()
    logger.info("=== AUTO-RETRAIN started at %s ===", started_at.isoformat())
    status = {
        "started_at": started_at.isoformat(),
        "status": "running",
        "retrain_id": log_row.id,
    }

    try:
        from app.training import retrain_pipeline

        metrics = retrain_pipeline() or {}
        status["metrics"] = metrics
        status["status"] = "success"

        log_row.status = "success"
        log_row.metrics_json = str(metrics)[:4000]
        logger.info("Retrain completed: %s", metrics)

    except ImportError:
        status["status"] = "skipped"
        status["reason"] = "training module not found"

        log_row.status = "skipped"
        log_row.message = "training module not found"
        logger.warning("Retrain skipped: training module not available")

    except Exception as e:
        status["status"] = "error"
        status["error"] = str(e)

        log_row.status = "error"
        log_row.error_message = str(e)[:2000]
        logger.exception("Retrain failed: %s", e)

    try:
        from app.redis_cache import redis_client, REDIS_AVAILABLE

        if REDIS_AVAILABLE:
            keys = redis_client.keys("sora:*")
            if keys:
                redis_client.delete(*keys)
            cache_cleared = len(keys)
            status["cache_cleared"] = cache_cleared
            logger.info("Cache invalidated: %d keys", cache_cleared)
        else:
            status["cache_cleared"] = 0
    except Exception as e:
        status["cache_cleared"] = 0
        logger.warning("Cache invalidation failed: %s", e)

    duration = round(time.time() - start_time, 2)
    finished_at = datetime.utcnow()
    status["duration_sec"] = duration
    status["finished_at"] = finished_at.isoformat()

    try:
        log_row.duration_sec = duration
        log_row.finished_at = finished_at
        db.add(log_row)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()

    _retrain_log.append(status)
    if len(_retrain_log) > 50:
        _retrain_log.pop(0)

    lock.release()
    return status


def scheduled_refresh_external_data():
    """Refresh external ESG data with distributed lock and DB logging."""
    from app.locks import RedisLock
    from app.database import SessionLocal, DataRefreshLog

    lock = RedisLock(key="sora:lock:external_refresh", timeout=300)
    if not lock.acquire():
        logger.warning("External refresh skipped: another refresh is already running")
        return {"status": "skipped", "reason": "lock_held"}

    db = SessionLocal()
    try:
        from app.external_data import refresh_live_data
        result = refresh_live_data() or {}

        log = DataRefreshLog(
            status=result.get("status", "success"),
            countries_fetched=int(result.get("countries_fetched", 0)),
            total_countries=int(result.get("total_countries", 0)),
            message=result.get("message"),
            job_name="external_data_refresh",
        )
        db.add(log)
        db.commit()

        logger.info(
            "External data refresh completed: %s",
            {
                "status": log.status,
                "countries_fetched": log.countries_fetched,
                "total_countries": log.total_countries,
            },
        )
        return result

    except Exception as e:
        db.rollback()
        try:
            db.add(
                DataRefreshLog(
                    status="error",
                    countries_fetched=0,
                    total_countries=0,
                    message=str(e)[:500],
                    job_name="external_data_refresh",
                )
            )
            db.commit()
        except Exception:
            pass

        logger.exception("External data refresh failed: %s", e)
        return {"status": "error", "message": str(e)}

    finally:
        lock.release()
        db.close()


def get_retrain_log():
    return list(_retrain_log)


def get_scheduler_status():
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "trigger": str(job.trigger),
            "next_run": str(job.next_run_time) if job.next_run_time else None,
        })
    return {
        "running": scheduler.running,
        "enabled": os.getenv("SORA_SCHEDULER", "1") == "1",
        "jobs": jobs,
        "retrain_history_count": len(_retrain_log),
    }


def init_scheduler():
    """Call from app startup."""
    if os.getenv("SORA_SCHEDULER", "1") != "1":
        logger.info("Scheduler disabled by SORA_SCHEDULER")
        return

    if scheduler.running:
        logger.info("Scheduler already running, skipping duplicate init")
        return

    scheduler.add_job(
        retrain_models,
        CronTrigger(hour=3, minute=0),
        id="auto_retrain_daily",
        name="Daily model retrain at 03:00 UTC",
        replace_existing=True,
    )

    scheduler.add_job(
        scheduled_refresh_external_data,
        IntervalTrigger(hours=12),
        id="auto_refresh_external_data",
        name="Refresh external ESG data every 12h",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "Scheduler started with %d jobs: %s",
        len(scheduler.get_jobs()),
        [job.id for job in scheduler.get_jobs()],
    )


def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down")
