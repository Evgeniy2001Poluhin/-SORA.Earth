"""AI Control Layer — audited write actions for AI agent."""
from datetime import datetime
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from app.auth import require_admin

router = APIRouter(prefix="/admin/ai", tags=["ai-control"])


class AIActionResponse(BaseModel):
    action: str
    status: str
    trigger_source: str = "ai_agent"
    timestamp: str
    details: Optional[dict] = None


@router.post("/refresh", response_model=AIActionResponse)
def ai_trigger_refresh(_admin=Depends(require_admin)):
    from app.external_data import refresh_live_data
    try:
        result = refresh_live_data(trigger_source="ai_agent")
        d = {"fetched": result.get("fetched", 0), "total": result.get("total", 0)}
        return AIActionResponse(action="data_refresh", status="success", timestamp=datetime.utcnow().isoformat(), details=d)
    except Exception as e:
        return AIActionResponse(action="data_refresh", status="error", timestamp=datetime.utcnow().isoformat(), details={"error": str(e)[:500]})


@router.post("/retrain", response_model=AIActionResponse)
def ai_trigger_retrain(_admin=Depends(require_admin)):
    from app.scheduler import retrain_models
    try:
        result = retrain_models(trigger_source="ai_agent")
        return AIActionResponse(action="model_retrain", status=result.get("status", "unknown"), timestamp=datetime.utcnow().isoformat(), details=result)
    except Exception as e:
        return AIActionResponse(action="model_retrain", status="error", timestamp=datetime.utcnow().isoformat(), details={"error": str(e)[:500]})


@router.post("/report", response_model=AIActionResponse)
def ai_generate_report(_admin=Depends(require_admin)):
    from app.database import SessionLocal, RetrainLog, DataRefreshLog, PredictionLog
    from sqlalchemy import func
    import json
    db = SessionLocal()
    try:
        retrain_total = db.query(RetrainLog).count()
        retrain_success = db.query(RetrainLog).filter(RetrainLog.status == "success").count()
        retrain_failed = db.query(RetrainLog).filter(RetrainLog.status == "failed").count()
        last_retrain = db.query(RetrainLog).order_by(RetrainLog.started_at.desc()).first()
        refresh_total = db.query(DataRefreshLog).count()
        refresh_success = db.query(DataRefreshLog).filter(DataRefreshLog.status == "success").count()
        pred_total = db.query(PredictionLog).count()
        avg_latency = db.query(func.avg(PredictionLog.latency_ms)).scalar()
        last_metrics = None
        if last_retrain and last_retrain.metrics_json:
            try:
                last_metrics = json.loads(last_retrain.metrics_json)
            except Exception:
                pass
        report = {
            "generated_at": datetime.utcnow().isoformat(),
            "retrain": {"total": retrain_total, "success": retrain_success, "failed": retrain_failed, "last_metrics": last_metrics},
            "data_refresh": {"total": refresh_total, "success": refresh_success},
            "predictions": {"total": pred_total, "avg_latency_ms": round(avg_latency, 2) if avg_latency else None},
        }
        return AIActionResponse(action="generate_report", status="success", timestamp=datetime.utcnow().isoformat(), details=report)
    finally:
        db.close()
