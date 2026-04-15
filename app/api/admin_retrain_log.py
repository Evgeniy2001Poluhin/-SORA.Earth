from typing import List, Optional
from datetime import datetime, timedelta
import json

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.database import SessionLocal, RetrainLog
from app.auth import require_admin


router = APIRouter(prefix="/admin", tags=["admin"])


class RetrainLogItem(BaseModel):
    id: int
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_sec: Optional[float] = None
    status: str
    trigger_source: Optional[str] = None
    job_name: Optional[str] = None
    model_version: Optional[str] = None
    data_version: Optional[str] = None
    message: Optional[str] = None
    error_message: Optional[str] = None
    metrics_json: Optional[str] = None

    class Config:
        from_attributes = True


class RetrainLogPage(BaseModel):
    items: List[RetrainLogItem]
    total: int
    page: int
    page_size: int


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/retrain-log", response_model=RetrainLogPage)
def list_retrain_log(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    trigger_source: Optional[str] = Query(None),
    db=Depends(get_db),
    _admin=Depends(require_admin),
):
    query = db.query(RetrainLog)
    if status:
        query = query.filter(RetrainLog.status == status)
    if trigger_source:
        query = query.filter(RetrainLog.trigger_source == trigger_source)
    total = query.count()
    items = (
        query.order_by(RetrainLog.started_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return RetrainLogPage(items=items, total=total, page=page, page_size=page_size)


@router.get("/timeline")
def admin_timeline(
    hours: int = Query(48, ge=1, le=24 * 30),
    limit: int = Query(50, ge=1, le=200),
    db=Depends(get_db),
    _admin=Depends(require_admin),
):
    since = datetime.utcnow() - timedelta(hours=hours)

    rows = (
        db.query(RetrainLog)
        .filter(RetrainLog.started_at >= since)
        .order_by(RetrainLog.started_at.desc())
        .limit(limit)
        .all()
    )

    events = []
    for r in rows:
        metrics = None
        try:
            metrics = json.loads(r.metrics_json) if r.metrics_json else None
        except Exception:
            metrics = None

        events.append({
            "type": "retrain",
            "timestamp": r.started_at.isoformat() if r.started_at else None,
            "status": r.status,
            "trigger_source": r.trigger_source,
            "job_name": r.job_name,
            "duration_sec": r.duration_sec,
            "model_version": r.model_version,
            "data_version": r.data_version,
            "message": r.message,
            "error_message": r.error_message,
            "metrics": metrics,
        })

    return {
        "events": events,
        "count": len(events),
        "hours": hours,
    }
