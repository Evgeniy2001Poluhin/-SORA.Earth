"""Compliance Sentinel API."""
from fastapi import APIRouter
from pydantic import BaseModel
from app.services.compliance import check

router = APIRouter(prefix="/api/v1/compliance", tags=["compliance"])


class CheckRequest(BaseModel):
    text: str


@router.post("/check")
def check_endpoint(req: CheckRequest):
    return check(req.text)
