"""SORA.Earth — Predict v2 endpoints (MLflow Registry champion model)."""
from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app import ml_registry

router = APIRouter(prefix="/api/v1", tags=["predict-v2"])


class ProjectV2(BaseModel):
    budget: float = Field(..., gt=0)
    co2_reduction: float = Field(..., ge=0)
    social_impact: float = Field(..., ge=0)
    duration_months: float = Field(..., gt=0)
    category: str = "water"
    region: str = "EU"


@router.post("/predict/v2")
def predict_v2(project: ProjectV2):
    """Production prediction via MLflow Registry (RF AUC 0.9892)."""
    p = ml_registry.predict_proba(project.model_dump())
    if p is None:
        return {
            "success_probability": None,
            "error": "registry_unavailable",
            "fallback_to_v1": True,
            "model": ml_registry.info(),
        }
    return {
        "success_probability": round(p, 4),
        "predicted_class": int(p >= 0.5),
        "model": ml_registry.info(),
    }


@router.get("/model/registry-info")
def registry_info():
    return ml_registry.info()
