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


def backend_base_url() -> str:
    """Where the backend answers on the internal network.

    The service has different names in the two compose files -- `app` in
    docker-compose.yml, `backend` in docker-compose.prod.yml -- so any
    hardcoded host is wrong in one of them. It was hardcoded to `app` on
    2026-07-17, and refresh_forecast_metrics has raised on every run in
    production since, twice a minute (#91). nginx was moved back to `backend`
    in the same commit; the scheduler was not.

    The default is production's name, because that is the environment where
    being wrong is expensive and unobserved. Both compose files set the
    variable explicitly so neither relies on this.

    Read at call time rather than at import so a test can set it without
    reloading the module.
    """
    return os.getenv("SORA_BACKEND_URL", "http://backend:8000").rstrip("/")

# From app.prom_metrics, where these actually live, and without a try/except.
#
# This read `from app.main import ...` for six counters that are not defined
# there. The ImportError was caught and all six were set to None, so every
# scheduler metric -- retrains, refreshes, drift, promotions, rejections -- was
# silently not recorded. The cost was still paid: the exception is raised after
# app.main has finished importing torch, SHAP and transformers.
#
# Three of the names also differ. The `_total` suffix belongs to the Prometheus
# metric name, not to the Python variable, so the aliases below are explicit
# rather than assumed:
#
#     sora_drift_detected   -> Counter("sora_drift_detected_total")
#     sora_model_promoted   -> Counter("sora_model_promoted_total")
#     sora_model_rejected   -> Counter("sora_model_rejected_total")
#
# No except: a missing counter is a fault to see, not one to swallow. That is
# what hid this for as long as it lasted.
from app.prom_metrics import (
    sora_retrain_total,
    sora_refresh_total,
    sora_full_pipeline_total,
    sora_drift_detected as sora_drift_detected_total,
    sora_model_promoted as sora_model_promoted_total,
    sora_model_rejected as sora_model_rejected_total,
)



def should_run_scheduler() -> bool:
    import os
    return os.getenv("RUN_SCHEDULER", "false").lower() == "true"

scheduler = BackgroundScheduler(timezone="UTC")

# Jobs that run once at startup, in addition to their schedule (#154).
#
# This is a contract, not a side effect, and it is stated here because nothing
# outside this file used to say it. **Recreating the scheduler container runs
# every job named here**, so an ordinary deployment -- and a rollback, which
# also recreates containers -- writes to the database and calls an external API.
#
# Not an APScheduler default. Measured: `IntervalTrigger(hours=24)` returns a
# first fire time a full interval away, so an interval job left alone does
# nothing at startup. These are forced with `modify_job(next_run_time=now)`
# below.
#
# The reason is that the alternative is worse for the operator: without it the
# first air-quality rows arrive an hour after a deployment, and a restart made
# specifically to check whether a source works produces nothing to look at for
# an hour.
#
# What it costs, stated so the trade is visible:
#
#   auto_run_ingesters                    writes rosstat + sber rows (idempotent
#                                         since #121: unchanged snapshots upsert
#                                         to a zero row delta)
#   auto_refresh_external_data            one call to the World Bank API per
#                                         deployment, rollbacks included
#   auto_openmeteo_ingestion              one Open-Meteo fetch
#   auto_openmeteo_air_quality_ingestion  one Open-Meteo fetch
#   refresh_forecast_metrics              reads only
#
# auto_openaq_ingestion is deliberately absent: the job is not registered unless
# SORA_OPENAQ_ENABLED is set, and an immediate run of a job that does not exist
# is not an error worth logging.
#
# tests/test_startup_jobs_contract.py pins the membership of this tuple. Adding
# a sixth entry has to be a decision rather than something that happens while
# editing nearby.
RUN_IMMEDIATELY_ON_STARTUP = (
    "auto_run_ingesters",
    "auto_refresh_external_data",
    "refresh_forecast_metrics",
    "auto_openmeteo_ingestion",
    "auto_openmeteo_air_quality_ingestion",
)


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
        if sora_retrain_total: sora_retrain_total.labels(status="success").inc()

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
    """
    Fetch scheduler status from Redis (published by scheduler container).
    Falls back to local scheduler if Redis unavailable or scheduler container not running.
    """
    import json
    from datetime import datetime

    # Try to get status from Redis first (scheduler container publishes it)
    try:
        from app.redis_cache import redis_client, REDIS_AVAILABLE

        if REDIS_AVAILABLE:
            logger.info("Redis available, trying to get scheduler status...")
            status_json = redis_client.get("sora:scheduler:status")
            logger.info("Redis status_json type: %s, value: %s", type(status_json), str(status_json)[:100] if status_json else None)
            if status_json:
                status = json.loads(status_json)

                # Add retrain history count
                try:
                    from app.database import SessionLocal, RetrainLog
                    db = SessionLocal()
                    status["retrain_history_count"] = db.query(RetrainLog).count()
                    db.close()
                except Exception:
                    status["retrain_history_count"] = 0

                status["source"] = "scheduler_container"
                return status
    except Exception as e:
        logger.warning("Failed to fetch scheduler status from Redis: %s", e)

    # Fallback: check local scheduler (will be empty in app container)
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "trigger": str(job.trigger),
            "next_run": str(job.next_run_time) if job.next_run_time else None,
        })

    if not jobs and not scheduler.running:
        # Local scheduler is empty and not running
        return {
            "running": False,
            "enabled": os.getenv("SORA_SCHEDULER", "1") == "1",
            "jobs": [],
            "jobs_count": 0,
            "error": "Scheduler container unreachable or not running",
            "source": "local_fallback",
            "retrain_history_count": 0,
        }

    # Local scheduler has jobs (shouldn't happen in app container, but handle it)
    return {
        "running": scheduler.running,
        "enabled": os.getenv("SORA_SCHEDULER", "1") == "1",
        "jobs": jobs,
        "jobs_count": len(jobs),
        "source": "local_scheduler",
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

        # Absolute quality gate: AUC must be >= 0.80
        MIN_AUC_THRESHOLD = 0.80
        if new_auc is not None and float(new_auc) < MIN_AUC_THRESHOLD:
            promoted = False
            reject_reason = f"AUC below minimum threshold: {new_auc:.4f} < {MIN_AUC_THRESHOLD}"
            if sora_model_rejected_total: sora_model_rejected_total.inc()
            logger.warning("Closed loop: model REJECTED - %s", reject_reason)
        elif old_auc is not None and new_auc is not None:
            auc_delta = float(new_auc) - float(old_auc)
            if auc_delta < -0.02:
                promoted = False
                reject_reason = "AUC degraded: %.4f -> %.4f (delta=%+.4f)" % (old_auc, new_auc, auc_delta)
                if sora_model_rejected_total: sora_model_rejected_total.inc()
                logger.warning("Closed loop: model REJECTED - %s", reject_reason)
            else:
                if sora_model_promoted_total: sora_model_promoted_total.inc()
                logger.info("Closed loop: model PROMOTED - AUC %.4f -> %.4f (delta=%+.4f)", float(old_auc), float(new_auc), auc_delta)
        else:
            # Old AUC unavailable: promote only if new_auc >= threshold
            if new_auc is not None and float(new_auc) >= MIN_AUC_THRESHOLD:
                if sora_model_promoted_total: sora_model_promoted_total.inc()
                logger.info("Closed loop: model PROMOTED (first model) - AUC %.4f", float(new_auc))
            else:
                logger.info("Closed loop: old AUC unavailable, new model meets threshold, auto-promoting")
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


def full_pipeline_run(trigger_source="full_pipeline", force: bool = False):
    """
    Complete MLOps pipeline: refresh → drift → retrain → AUC validate → promote/reject.
    """
    from app.locks import RedisLock
    lock = RedisLock(key="sora:lock:full_pipeline", timeout=900)
    if not lock.acquire() and not force:
        logger.warning("Full pipeline skipped: lock held")
        return {"status": "skipped", "reason": "lock_held"}
    try:
        if sora_full_pipeline_total: sora_full_pipeline_total.labels(status="started").inc()
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


def scheduled_run_ingesters():
    """Run all registered ingesters (Rosstat, Sber/VEB).

    OpenAQ and Open-Meteo are intentionally excluded: they are owned solely by
    their hourly scheduled_openaq_ingestion/scheduled_openmeteo_ingestion jobs.
    """
    from app.locks import RedisLock
    lock = RedisLock(key="sora:lock:ingesters", timeout=600)
    if not lock.acquire():
        logger.warning("Ingesters skipped: lock held")
        return {"status": "skipped", "reason": "lock_held"}
    try:
        from app.ingesters.runner import run_all_ingesters_sync
        result = run_all_ingesters_sync()
        logger.info("Ingesters done: %s signals", result.get("total_signals"))
        return result
    except Exception as e:
        logger.exception("Ingesters failed: %s", e)
        return {"status": "error", "error": str(e)}
    finally:
        lock.release()


#: The metrics this job trains, named once. The loop used to carry the list
#: inline while the session it read through was closed above it (#127).
PRETRAIN_METRICS = ("score", "prob", "co2_reduction")


def scheduled_pretrain_forecast_models():
    """Pre-train forecast models and warm the cache.

    Runs every 6 hours. Trains ensemble model on the latest evaluation data
    for each metric, storing fitted models in the forecast cache so API
    requests get instant responses.

    Also calculates and logs MAE/RMSE metrics to database for monitoring.
    """
    from app.locks import RedisLock
    lock = RedisLock(key="sora:lock:forecast_pretrain", timeout=300)
    if not lock.acquire():
        logger.warning("Forecast pretrain skipped: lock held")
        return {"status": "skipped"}
    try:
        import pandas as pd
        import numpy as np
        from app.database import SessionLocal, Evaluation, ForecastModelMetrics
        from app.services.forecasting import ModelRegistry, forecast_cache
        from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
        from datetime import datetime

        # Read everything training needs, then close the read transaction
        # before any model is fitted (#127).
        #
        # This used to close the session and go on using it: `db.close()` in a
        # finally, then `load_time_series(db, ...)` once per metric in the loop
        # below. A closed SQLAlchemy Session does not raise -- it acquires a
        # fresh connection and opens a new transaction -- and the reference was
        # then lost when `db` was rebound inside the loop, so nothing ever
        # ended them. Three metrics, three server connections held out of
        # pgbouncer's pool for the life of the process.
        #
        # The `finally: db.close()` sitting right above the loop is what made
        # that invisible on reading, which is why the shape being removed is
        # the rebinding, not only the missing close.
        #
        # One session for the whole function would fix the lost reference and
        # replace it with a read transaction held open across LSTM and Prophet
        # training. Reading is separated from training instead.
        from app.services.forecasting.data_loader import load_time_series

        with SessionLocal() as read_db:
            rows = read_db.query(Evaluation).order_by(Evaluation.created_at.asc()).all()

            if not rows or len(rows) < 10:
                logger.info("Forecast pretrain: insufficient data (%d rows)", len(rows) if rows else 0)
                return {"status": "skipped", "reason": "insufficient_data"}

            series_by_metric = {
                metric_name: load_time_series(read_db, metric_name)
                for metric_name in PRETRAIN_METRICS
            }
        # Read transaction closed. Nothing below holds a database connection
        # while a model is being fitted.

        metrics_trained = []

        for metric_name, df in series_by_metric.items():
            if len(df) < 3:
                continue

            try:
                start_time = time.time()

                # Split data for validation (80/20 train/test)
                train_size = int(len(df) * 0.8)
                train_df = df.iloc[:train_size]
                test_df = df.iloc[train_size:]

                # Train on full dataset for production
                forecaster = ModelRegistry.get("ensemble")
                forecaster.fit(df, target_col="y")

                # Calculate metrics on test set if we have enough data
                mae_val, rmse_val, r2_val, mape_val = None, None, None, None
                if len(test_df) >= 3:
                    # Predict on test set
                    test_predictions = []
                    for idx, row in test_df.iterrows():
                        # Use train data up to this point for prediction
                        hist_df = df[df['ds'] < row['ds']]
                        if len(hist_df) >= 3:
                            temp_forecaster = ModelRegistry.get("ensemble")
                            temp_forecaster.fit(hist_df, target_col="y")
                            pred = temp_forecaster.predict(horizon=1)
                            # pred is a ForecastResult object with .yhat list attribute
                            if pred and hasattr(pred, 'yhat') and len(pred.yhat) > 0:
                                test_predictions.append(pred.yhat[0])
                            elif pred and isinstance(pred, list) and len(pred) > 0:
                                # Fallback for old-style list response
                                test_predictions.append(pred[0]['yhat'])
                            else:
                                test_predictions.append(np.nan)

                    # Calculate metrics if we have predictions
                    if len(test_predictions) == len(test_df) and not all(np.isnan(test_predictions)):
                        y_true = test_df['y'].values
                        y_pred = np.array(test_predictions)

                        # Remove NaN predictions
                        valid_mask = ~np.isnan(y_pred)
                        y_true_clean = y_true[valid_mask]
                        y_pred_clean = y_pred[valid_mask]

                        if len(y_true_clean) > 0:
                            mae_val = mean_absolute_error(y_true_clean, y_pred_clean)
                            rmse_val = np.sqrt(mean_squared_error(y_true_clean, y_pred_clean))
                            r2_val = r2_score(y_true_clean, y_pred_clean)

                            # MAPE (avoid division by zero)
                            non_zero_mask = y_true_clean != 0
                            if non_zero_mask.sum() > 0:
                                mape_val = np.mean(np.abs((y_true_clean[non_zero_mask] - y_pred_clean[non_zero_mask]) / y_true_clean[non_zero_mask])) * 100

                training_duration = time.time() - start_time

                # Store in cache
                forecast_cache.store_fitted_model("ensemble", metric_name, df, forecaster, ttl=21600)

                # Log metrics to database: one short write transaction per
                # metric, opened after training rather than around it.
                #
                # Per-metric rather than one write for all three, deliberately:
                # today a failure on the third model leaves the first two
                # already committed, and collapsing them into a single
                # transaction would roll those back. That is a behaviour
                # change, and this fix is about a leaked connection.
                with SessionLocal.begin() as write_db:
                    metric_log = ForecastModelMetrics(
                        trained_at=datetime.utcnow(),
                        metric_name=metric_name,
                        model_type="ensemble",
                        train_samples=len(train_df) if len(test_df) >= 3 else len(df),
                        test_samples=len(test_df) if len(test_df) >= 3 else None,
                        mae=float(mae_val) if mae_val is not None else None,
                        rmse=float(rmse_val) if rmse_val is not None else None,
                        mape=float(mape_val) if mape_val is not None else None,
                        r2_score=float(r2_val) if r2_val is not None else None,
                        training_duration_sec=training_duration,
                        trigger_source="auto_scheduler",
                        status="success"
                    )
                    write_db.add(metric_log)

                # Update Prometheus metrics
                from app.prom_metrics import sora_forecast_mae, sora_forecast_rmse, sora_forecast_r2, sora_forecast_mape
                if mae_val is not None:
                    sora_forecast_mae.labels(metric=metric_name, model="ensemble").set(mae_val)
                if rmse_val is not None:
                    sora_forecast_rmse.labels(metric=metric_name, model="ensemble").set(rmse_val)
                if r2_val is not None:
                    sora_forecast_r2.labels(metric=metric_name, model="ensemble").set(r2_val)
                if mape_val is not None:
                    sora_forecast_mape.labels(metric=metric_name, model="ensemble").set(mape_val)

                metrics_trained.append(metric_name)
                logger.info(
                    "Forecast pretrain: ensemble/%s fitted (%d rows) - MAE=%.3f, RMSE=%.3f, R²=%.3f",
                    metric_name, len(df),
                    mae_val if mae_val is not None else -1,
                    rmse_val if rmse_val is not None else -1,
                    r2_val if r2_val is not None else -1
                )
            except Exception as e:
                logger.warning("Forecast pretrain failed for %s: %s", metric_name, e)

                # Log failure to database, in its own short transaction.
                #
                # This path closed its session correctly and still rebound
                # `db` inside the loop -- the shape that hid the leak on the
                # success path (#127). Converted with it so the name cannot
                # come back into the loop by either route.
                try:
                    with SessionLocal.begin() as write_db:
                        write_db.add(ForecastModelMetrics(
                            trained_at=datetime.utcnow(),
                            metric_name=metric_name,
                            model_type="ensemble",
                            train_samples=len(df) if 'df' in locals() else 0,
                            trigger_source="auto_scheduler",
                            status="failed",
                            error_message=str(e)[:500]
                        ))
                except Exception:
                    pass  # Don't fail if logging fails

        result = {"status": "ok", "metrics_trained": metrics_trained}
        logger.info("Forecast pretrain complete: %s", result)
        return result
    except Exception as e:
        logger.exception("Forecast pretrain failed: %s", e)
        return {"status": "error", "error": str(e)}
    finally:
        lock.release()


def refresh_forecast_metrics():
    """Keep Prometheus forecast metrics fresh for Grafana dashboard.

    Calls the LSTM status endpoint, whose handler sets three gauges as a side
    effect: sora_forecast_samples_total, sora_forecast_lstm_active and
    sora_forecast_days_remaining.

    They surface at **/metrics**, served from the prometheus_client registry by
    Instrumentator, and that is what Prometheus scrapes. This docstring used to
    name `/metrics/prometheus`, which returns 404 -- measured. The platform has
    two endpoints that look alike and are not:

        /metrics                    the real registry, 21 sora_* series
        /api/v1/metrics/prometheus  13 lines assembled by hand from an
                                    in-process dict, carrying none of the 22
                                    metrics declared in app/prom_metrics.py
        /metrics/prometheus         does not exist

    Checking the wrong one is how these gauges appeared to be missing after
    #93 fixed the call that sets them. See #94.
    """
    import requests

    try:
        response = requests.get(
            f"{backend_base_url()}/api/v1/lstm-status", timeout=10)

        if response.status_code == 200:
            data = response.json()
            logger.info(
                f"Forecast metrics refreshed via backend: "
                f"samples={data.get('samples')}, "
                f"lstm_active={data.get('active')}, "
                f"days_remaining={data.get('days_remaining')}"
            )
        else:
            logger.warning(f"LSTM status endpoint returned {response.status_code}")

    # A backend that is restarting, or a name that does not resolve, is not a
    # programming error, and a full traceback every 30 seconds is how a log
    # stops being read. Named separately so an unexpected failure still gets
    # its stack.
    except requests.exceptions.RequestException as e:
        logger.warning(
            "Forecast metrics refresh could not reach %s: %s",
            backend_base_url(), e)
    except Exception as e:
        logger.error(f"Failed to refresh forecast metrics: {e}", exc_info=True)


def init_scheduler(start: bool = True):
    """Register the scheduled jobs, and start the scheduler unless start=False.

    The ingestion jobs are given next_run_time=now below, so simply starting the
    scheduler fires live upstream requests immediately. Tests that only need to
    assert job registration pass start=False.
    """
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
        IntervalTrigger(hours=6),
        id="auto_refresh_external_data",
        name="Refresh external ESG data every 6h",
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


    scheduler.add_job(
        scheduled_run_ingesters,
        IntervalTrigger(hours=24),
        id="auto_run_ingesters",
        name="Run all ingesters every 24h",
        replace_existing=True,
    )

    scheduler.add_job(
        scheduled_pretrain_forecast_models,
        IntervalTrigger(hours=6),
        id="auto_pretrain_forecast",
        name="Pre-train forecast models every 6h",
        replace_existing=True,
    )

    scheduler.add_job(
        lambda: __import__("app.services.status_service", fromlist=["record_health"]).record_health(),
        IntervalTrigger(minutes=5), id="health_ping", replace_existing=True,
    )

    scheduler.add_job(
        refresh_forecast_metrics,
        IntervalTrigger(seconds=30),
        id="refresh_forecast_metrics",
        name="Refresh Prometheus forecast metrics every 30s",
        replace_existing=True,
    )

    # Environmental crisis detection — runs after ingesters
    try:
        from app.services.crisis_detector import detect_crises
        from app.database import SessionLocal as _SL

        def _scheduled_crisis_detection():
            db = _SL()
            try:
                count = detect_crises(db)
                logger.info("Crisis detection: %d violations found", count)
            except Exception:
                logger.exception("Crisis detection failed")
            finally:
                db.close()

        scheduler.add_job(
            _scheduled_crisis_detection,
            IntervalTrigger(hours=6),
            id="auto_crisis_detection",
            name="Detect environmental crises every 6h",
            replace_existing=True,
        )
        logger.info("Crisis detection job scheduled (every 6h)")
    except ImportError:
        logger.warning("crisis_detector not available, skipping job")

    # Environmental data ingestion jobs (Phase 1 Step 5)
    try:
        from app.services.environmental.scheduler_jobs import (
            scheduled_openaq_ingestion,
            scheduled_openmeteo_ingestion,
            scheduled_openmeteo_air_quality_ingestion,
            scheduled_source_health_check,
            scheduled_data_quality_aggregation,
        )

        # OpenAQ air quality ingestion -- stood down by default (#57).
        #
        # Every station within 25km of the 21 declared regions stopped
        # reporting in September 2017, measured with a working key and HTTP
        # 200 throughout. Scheduling it spends 21 requests an hour to produce
        # nothing: measured on production over 2026-08-04..08, 24 runs a day,
        # every one degraded. A warning that is normal every hour is not a
        # warning.
        #
        # The job is not registered rather than registered-and-skipping: a job
        # that exists and does nothing still shows up as a scheduled job.
        # Re-enabling needs SORA_OPENAQ_ENABLED and the condition recorded in
        # SOURCE_REGISTER["openaq"].reenable_condition -- the flag alone does
        # not make the stations report.
        from app.ingesters.source_register import (
            openaq_enabled, openaq_scheduling_refusal,
        )

        _openaq_refusal = openaq_scheduling_refusal()
        if _openaq_refusal is None:
            scheduler.add_job(
                scheduled_openaq_ingestion,
                IntervalTrigger(hours=1),
                id="auto_openaq_ingestion",
                name="Ingest OpenAQ air quality data every 1h",
                replace_existing=True,
            )
        elif _openaq_refusal == "enabled_without_api_key":
            # Loud: the operator asked for this source and cannot get it.
            # Registering anyway would schedule a fetch that returns an empty
            # list before it calls anything -- the same empty hourly run the
            # flag exists to stop, one layer down.
            logger.warning(
                "openaq ingestion not scheduled: SORA_OPENAQ_ENABLED is set "
                "but OPENAQ_API_KEY is missing or blank; set the key or unset "
                "the flag (see #57)"
            )
        else:
            logger.info(
                "openaq ingestion not scheduled: %s "
                "(set SORA_OPENAQ_ENABLED=true to override; see #57)",
                _openaq_refusal,
            )

        # Open-Meteo weather ingestion (every 1 hour)
        scheduler.add_job(
            scheduled_openmeteo_ingestion,
            IntervalTrigger(hours=1),
            id="auto_openmeteo_ingestion",
            name="Ingest Open-Meteo weather data every 1h",
            replace_existing=True,
        )

        # Open-Meteo air quality (every 1 hour), alongside OpenAQ rather than
        # instead of it. OpenAQ's stations for these regions stopped reporting in
        # September 2017, so it has produced nothing across hundreds of runs
        # (#56, #57) -- but retiring it before the two have been compared on real
        # production data swaps one gap for another that nobody has looked at.
        #
        # These values are modelled (CAMS reanalysis), not measured, and every
        # row says so. That is the honest cost of having data where the
        # instruments are dead.
        scheduler.add_job(
            scheduled_openmeteo_air_quality_ingestion,
            IntervalTrigger(hours=1),
            id="auto_openmeteo_air_quality_ingestion",
            name="Ingest Open-Meteo air quality data every 1h",
            replace_existing=True,
        )

        # Source health check (every 15 minutes)
        scheduler.add_job(
            scheduled_source_health_check,
            IntervalTrigger(minutes=15),
            id="auto_source_health_check",
            name="Check environmental source health every 15m",
            replace_existing=True,
        )

        # Data quality aggregation (every 1 hour)
        scheduler.add_job(
            scheduled_data_quality_aggregation,
            IntervalTrigger(hours=1),
            id="auto_data_quality_aggregation",
            name="Aggregate data quality metrics every 1h",
            replace_existing=True,
        )

        logger.info("Environmental ingestion jobs scheduled: OpenAQ (1h), Open-Meteo (1h), health (15m), quality (1h)")
    except ImportError as e:
        logger.warning("Environmental scheduler jobs not available: %s", e)

    from datetime import datetime, timezone
    _now = datetime.now(timezone.utc)
    for _jid in RUN_IMMEDIATELY_ON_STARTUP:
        try:
            scheduler.modify_job(_jid, next_run_time=_now)
            logger.info("Job %s scheduled to run immediately on startup", _jid)
        except Exception as e:
            logger.warning("Could not set immediate run for %s: %s", _jid, e)
    if not start:
        logger.info(
            "Scheduler jobs registered without starting: %s",
            [job.id for job in scheduler.get_jobs()],
        )
        return

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