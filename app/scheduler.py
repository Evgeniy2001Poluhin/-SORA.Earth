from typing import Optional


def _start_retrain_log(trigger_source: str = "manual", job_name: str = "model_retrain"):
    from app.database import SessionLocal, RetrainLog
    from datetime import datetime
    db = SessionLocal()
    try:
        row = RetrainLog(
            started_at=datetime.utcnow(),
            status="running",
            trigger_source=trigger_source,
            job_name=job_name,
            message="Retraining started",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


def _finish_retrain_log(
    log_id: int,
    status: str,
    message: Optional[str] = None,
    model_version: Optional[str] = None,
    data_version: Optional[str] = None,
    metrics: Optional[dict] = None,
    error_message: Optional[str] = None,
):
    from app.database import SessionLocal, RetrainLog
    from datetime import datetime
    import json
    db = SessionLocal()
    try:
        row = db.query(RetrainLog).filter(RetrainLog.id == log_id).first()
        if not row:
            return
        finished_at = datetime.utcnow()
        row.finished_at = finished_at
        row.duration_sec = (finished_at - row.started_at).total_seconds() if row.started_at else None
        row.status = status
        row.message = message
        row.model_version = model_version
        row.data_version = data_version
        row.metrics_json = json.dumps(metrics, ensure_ascii=False) if metrics else None
        row.error_message = error_message
        db.commit()
    finally:
        db.close()

import json
import os
import logging
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)


def should_run_scheduler() -> bool:
    import os
    return os.getenv("RUN_SCHEDULER", "false").lower() == "true"

scheduler = BackgroundScheduler(timezone="UTC")


def retrain_models(trigger_source: str = "manual"):
    """Auto-retrain all ML models, invalidate cache, and persist retrain log to DB."""
    from app.locks import RedisLock

    log_id = _start_retrain_log(trigger_source=trigger_source, job_name="model_retrain")
    lock = RedisLock(key="sora:lock:model_retrain", timeout=600)

    if not lock.acquire():
        logger.warning("Retrain skipped: another retrain is already running")
        _finish_retrain_log(
            log_id=log_id,
            status="skipped",
            message="Retrain skipped: lock already held",
            metrics={"status": "skipped", "reason": "lock_held"},
        )
        return {"status": "skipped", "reason": "lock_held"}

    started_at = datetime.utcnow()
    start_time = time.time()
    logger.info("=== AUTO-RETRAIN started at %s ===", started_at.isoformat())

    status = {
        "started_at": started_at.isoformat(),
        "status": "running",
        "retrain_id": log_id,
    }

    try:
        from app.api.retrain import _do_retrain

        result = _do_retrain(min_samples=50, trigger_source=trigger_source)
        metrics = result.get("metrics", {}) if isinstance(result, dict) else {}
        status["metrics"] = metrics
        status["status"] = "success"

        _finish_retrain_log(
            log_id=log_id,
            status="success",
            message="Retraining completed successfully",
            metrics=metrics,
        )
        logger.info("Retrain completed: %s", metrics)

    except ImportError:
        status["status"] = "skipped"
        status["reason"] = "training module not found"

        _finish_retrain_log(
            log_id=log_id,
            status="skipped",
            message="training module not found",
            metrics={"status": "skipped", "reason": "training_module_not_found"},
        )
        logger.warning("Retrain skipped: training module not available")

    except Exception as e:
        status["status"] = "error"
        status["error"] = str(e)

        _finish_retrain_log(
            log_id=log_id,
            status="failed",
            message="Retraining failed",
            error_message=str(e),
        )
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
        _finish_retrain_log(
            log_id=log_id,
            status="success" if status["status"] == "success" else ("skipped" if status["status"] == "skipped" else "failed"),
            message="Retraining completed successfully" if status["status"] == "success" else status.get("reason", "Retraining failed"),
            metrics=status.get("metrics"),
            error_message=status.get("error"),
        )
    except Exception:
        pass
    finally:
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
        result = refresh_live_data(trigger_source="auto_scheduler") or {}

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

def get_retrain_log(limit: int = 20):
    from app.database import SessionLocal, RetrainLog
    import json

    db = SessionLocal()
    try:
        rows = (
            db.query(RetrainLog)
            .order_by(RetrainLog.started_at.desc())
            .limit(limit)
            .all()
        )

        result = []
        for r in rows:
            try:
                metrics = json.loads(r.metrics_json) if r.metrics_json else None
            except Exception:
                metrics = r.metrics_json

            result.append({
                "id": r.id,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "duration_sec": r.duration_sec,
                "status": r.status,
                "trigger_source": r.trigger_source,
                "job_name": r.job_name,
                "model_version": r.model_version,
                "data_version": r.data_version,
                "message": r.message,
                "error_message": r.error_message,
                "metrics": metrics,
            })
        return result
    finally:
        db.close()


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
        "retrain_history_count": (
            __import__("app.database", fromlist=["SessionLocal", "RetrainLog"]).SessionLocal().query(
                __import__("app.database", fromlist=["SessionLocal", "RetrainLog"]).RetrainLog
            ).count()
        ),
    }


def closed_loop_retrain(trigger_source="scheduler_closed_loop"):
    from app.locks import RedisLock
    lock = RedisLock(key="sora:lock:closed_loop", timeout=600)
    if not lock.acquire():
        logger.warning("Closed loop skipped: lock held")
        return {"status": "skipped", "reason": "lock_held"}
    try:
        from app.api.drift import check_drift
        drift_result = check_drift(window=50)
        drift_detected = bool(drift_result.get("drift_detected", False))
        if not drift_detected:
            logger.info("Closed loop: no drift, skipping retrain")
            return {"status": "ok", "drift_detected": False, "retrained": False, "reason": "drift_not_detected"}
        old_auc = None
        try:
            from app.api.retrain import _get_current_metrics
            old_m = _get_current_metrics()
            old_auc = old_m.get("auc_roc") or old_m.get("roc_auc")
        except Exception:
            pass
        from app.api.retrain import _do_retrain
        result = _do_retrain(min_samples=50, trigger_source=trigger_source)
        new_metrics = result.get("metrics", {}) if isinstance(result, dict) else {}
        new_auc = new_metrics.get("auc_roc") or new_metrics.get("roc_auc")
        promoted = True
        reject_reason = None
        if old_auc is not None and new_auc is not None:
            auc_delta = float(new_auc) - float(old_auc)
            if auc_delta < -0.02:
                promoted = False
                reject_reason = "AUC degraded: %.4f -> %.4f (delta=%+.4f)" % (old_auc, new_auc, auc_delta)
                logger.warning("Closed loop: model REJECTED - %s", reject_reason)
            else:
                logger.info("Closed loop: model PROMOTED - AUC %.4f -> %.4f (delta=%+.4f)", float(old_auc), float(new_auc), auc_delta)
        else:
            logger.info("Closed loop: old AUC unavailable, auto-promoting")
        log_id = _start_retrain_log(trigger_source=trigger_source, job_name="closed_loop")
        _finish_retrain_log(
            log_id=log_id,
            status="promoted" if promoted else "rejected",
            message="Closed loop: %s" % ("promoted" if promoted else "rejected"),
            metrics={"old_auc": float(old_auc) if old_auc else None, "new_auc": float(new_auc) if new_auc else None, "promoted": promoted, "reject_reason": reject_reason},
        )
        return {"status": "ok", "drift_detected": True, "retrained": True, "promoted": promoted, "old_auc": float(old_auc) if old_auc else None, "new_auc": float(new_auc) if new_auc else None, "reject_reason": reject_reason}
    except Exception as e:
        logger.exception("Closed loop failed: %s", e)
        return {"status": "error", "error": str(e)}
    finally:
        lock.release()


def full_pipeline_run(trigger_source="full_pipeline"):
    """
    Complete MLOps pipeline: refresh → drift → retrain → AUC validate → promote/reject.
    """
    from app.locks import RedisLock
    lock = RedisLock(key="sora:lock:full_pipeline", timeout=900)
    if not lock.acquire():
        logger.warning("Full pipeline skipped: lock held")
        return {"status": "skipped", "reason": "lock_held"}
    try:
        logger.info("Full pipeline: step 1 — refresh external data")
        refresh_result = {}
        try:
            from app.external_data import refresh_live_data
            refresh_result = refresh_live_data(trigger_source=trigger_source) or {}
        except Exception as e:
            logger.warning("Full pipeline: refresh failed (%s), continuing", e)
            refresh_result = {"status": "error", "error": str(e)}

        logger.info("Full pipeline: step 2 — closed loop (drift → retrain → validate)")
        loop_result = closed_loop_retrain(trigger_source=trigger_source)

        return {
            "status": "ok",
            "pipeline": "full",
            "refresh_result": refresh_result,
            "closed_loop_result": loop_result,
        }
    except Exception as e:
        logger.exception("Full pipeline failed: %s", e)
        return {"status": "error", "error": str(e)}
    finally:
        lock.release()

def init_scheduler():
    if not should_run_scheduler():
        logger.info("RUN_SCHEDULER is false, scheduler will not be started in this process")
        return

    """Call from app startup."""
    if os.getenv("SORA_SCHEDULER", "1") != "1":
        logger.info("Scheduler disabled by SORA_SCHEDULER")
        return

    if scheduler.running:
        logger.info("Scheduler already running, skipping duplicate init")
        return

    scheduler.add_job(
        closed_loop_retrain,
        CronTrigger(hour=3, minute=0),
        id="auto_closed_loop_daily",
        name="Daily closed-loop: drift -> retrain -> validate at 03:00 UTC",
        replace_existing=True,
    )

    scheduler.add_job(
        scheduled_refresh_external_data,
        IntervalTrigger(hours=12),
        id="auto_refresh_external_data",
        name="Refresh external ESG data every 12h",
        replace_existing=True,
    )

    scheduler.add_job(
        full_pipeline_run,
        CronTrigger(day_of_week="sun", hour=3, minute=30),
        kwargs={"trigger_source": "auto_full_pipeline_weekly"},
        id="auto_full_pipeline_weekly",
        name="Weekly full pipeline: refresh -> drift -> retrain -> validate at Sun 03:30 UTC",
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