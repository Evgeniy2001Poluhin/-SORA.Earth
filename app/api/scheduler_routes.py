from fastapi import APIRouter
from app.scheduler import get_scheduler_status, get_retrain_log, retrain_models

router = APIRouter()

@router.get("/scheduler/status", summary="Scheduler status and jobs")
def scheduler_status():
    return get_scheduler_status()

@router.get("/scheduler/retrain/history", summary="Retrain history log")
def retrain_history():
    return get_retrain_log()

@router.post("/scheduler/retrain/trigger", summary="Trigger manual retrain now")
def trigger_retrain():
    result = retrain_models()
    return result
