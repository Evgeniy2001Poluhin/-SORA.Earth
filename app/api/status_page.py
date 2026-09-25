from fastapi import APIRouter
from app.services.status_service import status_summary
router = APIRouter(prefix="/api/v1", tags=["status"])

@router.get("/status/uptime", summary="Public status page data (current component status)")
def status_uptime():
    # The path keeps its old name because the SPA calls it; it no longer returns uptime.
    return status_summary()
