"""SORA.Earth - Co-Pilot API endpoints."""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from app.services.copilot import explain_prediction, health as copilot_health

router = APIRouter()


class CopilotRequest(BaseModel):
    probability: float
    features: Dict[str, Any]
    shap_values: Optional[List[Dict[str, Any]]] = None
    project: Optional[Dict[str, Any]] = None
    model_version: Optional[str] = "v5"


@router.post("/copilot/explain", summary="LLM Co-Pilot: human-readable ESG prediction explanation")
def explain(payload: CopilotRequest):
    return explain_prediction(
        probability=payload.probability,
        features=payload.features,
        shap_values=payload.shap_values,
        project=payload.project,
        model_version=payload.model_version or "v5",
    )


@router.get("/copilot/health", summary="Co-Pilot health and configuration")
def health():
    return copilot_health()
