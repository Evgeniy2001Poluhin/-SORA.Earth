import logging
import time
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()
_retrain_log = []


def retrain_models():
    """Auto-retrain all ML models and invalidate cache."""
    start = time.time()
    logger.info("=== AUTO-RETRAIN started at %s ===", datetime.utcnow().isoformat())
    status = {"started_at": datetime.utcnow().isoformat(), "status": "running"}

    try:
        # 1. Retrain
        from app.training import retrain_pipeline
        metrics = retrain_pipeline()
        status["metrics"] = metrics
        status["status"] = "success"
        logger.info("Retrain completed: %s", metrics)

    except ImportError:
        # training module not ready yet — simulate
        status["status"] = "skipped"
        status["reason"] = "appraining module not found"
        logger.warning("Retrain skipped: training module not available")

    except Exception as e:
        status["status"] = "error"
        status["error"] = str(e)
        logger.exception("Retrain failed: %s", e)

    # 2. Invalidate Redis cache
    try:
        from app.redis_cache import redis_client, REDIS_AVAILABLE
        if REDIS_AVAILABLE:
            keys = redis_client.keys('sora:*')
            if keys:
                redis_client.delete(*keys)
            status["cache_cleared"] = len(keys)
            logger.info("Cache invalidated: %d keys", len(keys))
        else:
            status["cache_cleared"] = 0
    except Exception as e:
        status["cache_cleared"] = 0
        logger.warning("Cache invalidation failed: %s", e)

    status["duration_sec"] = round(time.time() - start, 2)
    status["finished_at"] = datetime.utcnow().isoformat()
    _retrain_log.append(status)
    if len(_retrain_log) > 50:
        _retrain_log.pop(0)
    return status


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
        "jobs": jobs,
        "retrain_history_count": len(_retrain_log),
    }


def init_scheduler():
    """Call from app startup."""
    scheduler.add_job(
        retrain_models,
        CronTrigger(hour=3, minute=0),
        id="auto_retrain_daily",
        name="Daily model retrain at 03:00 UTC",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started with %d jobs", len(scheduler.get_jobs()))


def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down")
