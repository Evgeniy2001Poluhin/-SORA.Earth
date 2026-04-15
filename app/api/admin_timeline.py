"""Admin timeline."""
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import SessionLocal, RetrainLog, DataRefreshLog

router = APIRouter(prefix="/admin", tags=["admin"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/timeline")
def admin_timeline(
    hours: int = Query(72, ge=1, le=720),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    since = datetime.utcnow() - timedelta(hours=hours)
    events = []
    retrains = db.query(RetrainLog).filter(RetrainLog.started_at >= since).order_by(RetrainLog.started_at.desc()).limit(limit).all()
    for r in retrains:
        events.append({"type": "retrain", "timestamp": r.started_at.isoformat() if r.started_at else None, "status": r.status, "trigger_source": r.trigger_source, "duration_sec": r.duration_sec, "model_version": r.model_version, "message": r.message, "error_message": r.error_message, "metrics": json.loads(r.metrics_json) if r.metrics_json else None})
    refreshes = db.query(DataRefreshLog).filter(DataRefreshLog.timestamp >= since).order_by(DataRefreshLog.timestamp.desc()).limit(limit).all()
    for r in refreshes:
        events.append({"type": "data_refresh", "timestamp": r.timestamp.isoformat() if r.timestamp else None, "status": r.status, "trigger_source": getattr(r, "trigger_source", "auto"), "countries_fetched": r.countries_fetched, "total_countries": r.total_countries, "message": r.message, "error_message": r.error_message})
    events.sort(key=lambda e: e["timestamp"] or "", reverse=True)
    return {"hours": hours, "total_events": len(events[:limit]), "events": events[:limit]}
