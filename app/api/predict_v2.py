"""SORA.Earth — Predict v2 endpoints (MLflow Registry champion model)."""
from typing import Optional
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.schemas import PredictV2Ok, PredictV2Unavailable

from app import ml_registry

router = APIRouter(prefix="/api/v1", tags=["predict-v2"])


class ProjectV2(BaseModel):
    budget: float = Field(..., gt=0)
    co2_reduction: float = Field(..., ge=0)
    social_impact: float = Field(..., ge=0)
    duration_months: float = Field(..., gt=0)
    category: str = "water"
    region: str = "EU"


@router.post(
    "/predict/v2",
    response_model=PredictV2Ok,
    responses={
        503: {
            "model": PredictV2Unavailable,
            "description": "The model registry could not answer.",
        }
    },
)
def predict_v2(project: ProjectV2):
    """Production prediction via the MLflow registry champion.

    **An unreachable registry is a 503, not a prediction.** This used to answer
    200 with `success_probability: null`, no `predicted_class`, and
    `fallback_to_v1: true`. On a screen a null probability renders as 0, and a
    missing `predicted_class` reads as 0 too -- both of which say "this project
    will fail". An outage and a negative verdict looked the same, and only one
    of them was true.

    `fallback_to_v1` is gone. Nothing in this handler falls back to v1, and the
    flag was read nowhere: one write, zero reads, measured across the
    repository and the built frontend. A field naming an action nobody performs
    is worse than no field.

    Returned as a `JSONResponse` on the failure branch rather than the model,
    for the reason `app/api/drift.py` gives: `response_model` is the success
    shape, and `responses={503: ...}` is what keeps the other one in the
    schema instead of hand-written prose.
    """
    p = ml_registry.predict_proba(project.model_dump())
    if p is None:
        return JSONResponse(
            status_code=503,
            content=PredictV2Unavailable(
                reason_code="registry_unavailable",
                detail="The model registry is not available; no prediction was made.",
                model=ml_registry.info(),
            ).model_dump(),
        )
    return PredictV2Ok(
        success_probability=round(p, 4),
        predicted_class=int(p >= 0.5),
        model=ml_registry.info(),
    )


@router.get("/model/registry-info")
def registry_info():
    return ml_registry.info()
