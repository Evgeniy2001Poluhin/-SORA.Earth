from typing import Optional
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import require_admin
from app.database import SessionLocal, RetrainLog, DataRefreshLog
from app.external_data import get_refresh_status
from app.drift_detection import drift_detector
from app.mlflow_tracking import get_experiment_stats
from app.scheduler import get_scheduler_status


router = APIRouter(prefix="/admin", tags=["admin"])


class ModelSnapshot(BaseModel):
    experiment: Optional[str] = None
    total_runs: Optional[int] = None
    tracking_uri: Optional[str] = None
    model_version: Optional[str] = None
    ensemble_cv_auc: Optional[float] = None
    rf_cv_auc: Optional[float] = None
    xgb_cv_auc: Optional[float] = None


class DataSnapshot(BaseModel):
    status: Optional[str] = None
    running: Optional[bool] = None
    last_refresh: Optional[str] = None
    countries_refreshed: Optional[int] = None
    static_countries: Optional[int] = None
    refresh_log_total: Optional[int] = None
    refresh_success_count: Optional[int] = None
    refresh_failed_count: Optional[int] = None
    last_refresh_status: Optional[str] = None
    last_refresh_at: Optional[str] = None
    last_ron_sec: Optional[float] = None
    last_refresh_trigger: Optional[str] = None


class DriftSnapshot(BaseModel):
    status: Optional[str] = None
    drift_detected: Optional[bool] = None
    drift_score: Optional[float] = None
    observations: Optional[int] = None
    drifted_features_count: Optional[int] = None


class SchedulerSnapshot(BaseModel):
    running: Optional[bool] = None
    enabled: Optional[bool] = None
    jobs_count: Optional[int] = None
    retrain_history_count: Optional[int] = None
    next_run_at: Optional[str] = None


class RetrainLogSummary(BaseModel):
    total: int
    success_count: Optional[int] = None
    failed_count: Optional[int] = None
    last_status: Optional[str] = None
    last_error_message: Optional[str] = None
    last_run_at: Optional[datetime] = None
    last_model_version: Optional[str] = None


class AdminSnapshot(BaseModel):
    model: ModelSnapshot
    data: DataSnapshot
    drift: DriftSnapshot
    scheduler: SchedulerSnapshot
    retrain_log_summary: RetrainLogSummary


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/snapshot", response_model=AdminSnapshot)
def get_admin_snapshot(
    db=Depends(get_db),
    _admin=Depends(require_admin),
):
    total = db.query(RetrainLog).count()
    last = (
        db.query(RetrainLog)
        .order_by(RetrainLog.started_at.desc())
        .first()
    )

    retrain_success = db.query(RetrainLog).filter(RetrainLog.status == "success").count()
    retrain_failed = db.query(RetrainLog).filter(RetrainLog.status == "failed").count()

    retrain_summary = RetrainLogSummary(
        total=total,
        success_count=retrain_success,
        failed_count=retrain_failed,
        last_status=getattr(last, "status", None),
        last_error_message=getattr(last, "message", None),
        last_run_at=getattr(last, "started_at", None),
        last_model_version=getattr(last, "model_version", None),
    )

    model_stats = {}
    try:
        model_stats = get_experiment_stats() or {}
    except Exception:
        model_stats = {}

    model_snapshot = ModelSnapshot(
        experiment=model_stats.get("experiment"),
        total_runs=model_stats.get("total_runs"),
        tracking_uri=model_stats.get("tracking_uri"),
        model_version=getattr(last, "model_version", None),
        ensemble_cv_auc=model_stats.get("ensemble_cv_auc"),
        rf_cv_auc=model_stats.get("rf_cv_auc"),
        xgb_cv_auc=model_stats.get("xgb_cv_auc"),
    )

    refresh = {}
    try:
        refresh = get_refresh_status() or {}
    except Exception:
        refresh = {}

    refresh_log_total = db.query(DataRefreshLog).count()
    refresh_success = db.query(DataRefreshLog).filter(DataRefreshLog.status == "success").count()
    refresh_failed = db.query(DataRefreshLog).filter(DataRefreshLog.status == "failed").count()
    last_refresh_log = (
        db.query(DataRefreshLog)
        .order_by(DataRefreshLog.timestamp.desc())
        .first()
    )

    data_snapshot = DataSnapshot(
        status=refresh.get("status"),
        running=refresh.get("running"),
        last_refresh=refresh.get("last_refresh"),
        countries_refreshed=refresh.get("countries_refreshed"),
        static_countries=refresh.get("static_countries"),
        refresh_log_total=refresh_log_total,
        refresh_success_count=refresh_success,
        refresh_failed_count=refresh_failed,
        last_refresh_status=getattr(last_refresh_log, "status", None),
        last_refresh_at=(
            last_refresh_log.timestamp.isoformat()
            if last_refresh_log and last_refresh_log.timestamp else None
        ),
    )

    drift = {}
    try:
        drift = drift_detector.check_drift() or {}
    except Exception:
        drift = {}

    drifted_features = drift.get("drifted_features") or []
    drift_snapshot = DriftSnapshot(
        status=drift.get("status"),
        drift_detected=drift.get("drift_detected"),
        drift_score=drift.get("drift_score"),
        observations=drift.get("observations"),
        drifted_features_count=len(drifted_features),
    )

    sched = {}
    try:
        sched = get_scheduler_status() or {}
    except Exception:
        sched = {}

    jobs = sched.get("jobs") or []
    next_run_at = jobs[0].get("next_run") if jobs else None

    scheduler_snapshot = SchedulerSnapshot(
        running=sched.get("running"),
        enabled=sched.get("enabled"),
        jobs_count=len(jobs),
        retrain_history_count=sched.get("retrain_history_count"),
        next_run_at=next_run_at,
    )

    return AdminSnapshot(
        model=model_snapshot,
        data=data_snapshot,
        drift=drift_snapshot,
        scheduler=scheduler_snapshot,
        retrain_log_summary=retrain_summary,
    )
