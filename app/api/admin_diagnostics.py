"""Admin diagnostics — extended health report."""
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import (SessionLocal, RetrainLog, DataRefreshLog, PredictionLog,
                          count_physical_runs)

router = APIRouter(prefix="/admin", tags=["admin"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/diagnostics")
def admin_diagnostics(
    hours: int = Query(24, ge=1, le=720),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    since = datetime.utcnow() - timedelta(hours=hours)
    # Runs, not rows (#199 contract point 10). One closed-loop cycle writes two
    # rows -- the training and the decision -- and `retrain_models` writes two
    # both saying `success`, so `.count()` here reported one run as two.
    #
    # `count_physical_runs` groups by `run_id` and was written for exactly this,
    # with `tests/test_canonical_run_id.py` behind it. It already backs
    # `/admin/ai-control`; this endpoint was the reader that still counted rows,
    # and the structural tests over it never looked at a value (#199).
    retrain_total = count_physical_runs(db)
    retrain_success = count_physical_runs(db, status="success")
    retrain_failed = count_physical_runs(db, status="failed")
    retrain_recent = count_physical_runs(db, since=since)
    last_retrain = db.query(RetrainLog).order_by(RetrainLog.started_at.desc()).first()
    refresh_total = db.query(DataRefreshLog).count()
    refresh_success = db.query(DataRefreshLog).filter(DataRefreshLog.status == "success").count()
    refresh_errors = db.query(DataRefreshLog).filter(DataRefreshLog.status == "error").count()
    refresh_recent = db.query(DataRefreshLog).filter(DataRefreshLog.timestamp >= since).count()
    last_refresh = db.query(DataRefreshLog).order_by(DataRefreshLog.timestamp.desc()).first()
    prediction_total = db.query(PredictionLog).count()
    pred_recent = db.query(PredictionLog).filter(PredictionLog.timestamp >= since).count()
    avg_latency = db.query(func.avg(PredictionLog.latency_ms)).filter(PredictionLog.timestamp >= since).scalar()
    top_errors = db.query(RetrainLog.error_message, func.count(RetrainLog.id)).filter(RetrainLog.status == "failed", RetrainLog.error_message.isnot(None)).group_by(RetrainLog.error_message).order_by(func.count(RetrainLog.id).desc()).limit(5).all()
    last_metrics = None
    if last_retrain and last_retrain.metrics_json:
        try:
            last_metrics = json.loads(last_retrain.metrics_json)
        except Exception:
            pass
    return {
        "period_hours": hours,
        "retrain": {"total": retrain_total, "success": retrain_success, "failed": retrain_failed, "recent": retrain_recent, "last_status": getattr(last_retrain, "status", None), "last_at": last_retrain.started_at.isoformat() if last_retrain and last_retrain.started_at else None, "last_metrics": last_metrics},
        "data_refresh": {"total": refresh_total, "success": refresh_success, "errors": refresh_errors, "recent": refresh_recent, "last_status": getattr(last_refresh, "status", None), "last_at": last_refresh.timestamp.isoformat() if last_refresh and last_refresh.timestamp else None},
        "predictions": {"total": prediction_total, "recent": pred_recent, "avg_latency_ms": round(avg_latency, 2) if avg_latency else None},
        "top_retrain_errors": [{"error": e, "count": c} for e, c in top_errors],
    }
