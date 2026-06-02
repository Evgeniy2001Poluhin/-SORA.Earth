from fastapi import APIRouter
from app.services.status_service import status_summary
router = APIRouter(prefix="/api/v1", tags=["status"])

@router.get("/status/uptime", summary="Public status page data (uptime 24h/7d)")
def status_uptime():
    return status_summary()
