"""API routes for AI Teammate."""
from fastapi import APIRouter, Depends, Query
from app.auth import require_admin
from app.agents.ai_teammate import AITeammate
from dataclasses import asdict

router = APIRouter(prefix="/admin/ai-teammate", tags=["ai-teammate"])


@router.post("/run")
def run_teammate(
    mode: str = Query("observe", pattern="^(observe|auto)$"),
    _admin=Depends(require_admin),
):
    """Run AI Teammate: observe (read-only) or auto (read + execute)."""
    teammate = AITeammate(mode=mode)
    report = teammate.run()
    return asdict(report)


@router.get("/status")
def teammate_status(_admin=Depends(require_admin)):
    """Quick observe-only status check."""
    teammate = AITeammate(mode="observe")
    report = teammate.run()
    return asdict(report)
