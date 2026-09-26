"""FastAPI routes powered by MLflow Registry model."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from app.auth import require_admin
from mlflow.exceptions import MlflowException
import logging
import time

from app.obs.request_log import log_prediction

from app.ml.registry_loader import get_model, get_version, get_alias, reload as reload_model, get_bundle, RegistryException
from app.ml.features import build_features, UnknownCategoryError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v2", tags=["ml-v2"])


class PredictRequest(BaseModel):
    budget: float = Field(..., gt=0)
    co2_reduction: float = Field(..., ge=0)
    social_impact: float = Field(..., ge=0, le=10)
    duration_months: int = Field(..., ge=1, le=120)
    category: str = "energy"
    region: str = "EU"


class PredictResponse(BaseModel):
    success_probability: float = Field(
        ..., description=(
            "Model estimate of a **heuristic** label, not a validated outcome. `success` is derived from a project's administrative status and closing date, and `duration_months` is a second reading of the same fact -- so most of the model's apparent skill is that leak. Measured 2026-09-20: ROC AUC 0.9165 with every feature, 0.8700 from budget and duration alone, 0.6605 with the closing-date-derived column removed. Use it to rank and to explain, not as evidence a project will succeed (#232, resolved as 'retire the quality claim')."
        )
    )
    success_class: int
    model_version: str
    model_name: str = "esg-success-predictor"
    alias: str = "champion"


@router.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    try:
        t0 = time.perf_counter()

        # Get the bundle (model + preprocessing artifacts)
        bundle = get_bundle()

        if bundle is None:
            # SORA_OFFLINE=1
            return JSONResponse(
                status_code=503,
                content={
                    "reason_code": "registry_unavailable",
                    "detail": "The model registry is not available; no prediction was made."
                }
            )

        # Build features using the bundle's preprocessor
        X = build_features(req.dict(), bundle)

        # Predict
        model = bundle["model"]
        proba = float(model.predict_proba(X)[0][1])
        cls = int(round(proba))

        latency_ms = (time.perf_counter() - t0) * 1000.0
        mv = str(bundle["version"])

        log_prediction(
            features=req.dict(),
            probability=proba,
            label=cls,
            latency_ms=latency_ms,
            model_version=mv,
            model_alias="champion",
        )

        return PredictResponse(
            success_probability=round(proba, 4),
            success_class=cls,
            model_version=mv,
        )

    except RegistryException as e:
        # Model registry or preprocessing artifacts unavailable
        logger.warning("Registry exception: %s - %s", e.reason_code, e.detail)
        return JSONResponse(
            status_code=503,
            content={
                "reason_code": e.reason_code,
                "detail": e.detail
            }
        )

    except UnknownCategoryError as e:
        # Unknown category or region
        logger.warning("Unknown %s: %s (known: %s)", e.field, e.value, e.known_values)
        return JSONResponse(
            status_code=422,
            content={
                "detail": f"Unknown {e.field}: {e.value}. Known {e.field}s: {', '.join(e.known_values)}"
            }
        )

    except Exception as e:
        logger.exception("v2 predict failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/model/version")
def model_version():
    try:
        return {
            "name": "esg-success-predictor",
            "alias": get_alias(),
            "version": str(get_version()),
        }
    except MlflowException as e:
        # get_version() -> get_model() -> registry_loader._load() looks up
        # `models:/{name}@{alias}` in the MLflow Model Registry -- a separate
        # thing from the models/model.pkl file that actually serves
        # /api/v1/predict, and nothing here has ever registered a model under
        # that name+alias on this server. Unguarded, this was an unhandled
        # RestException ("Registered model alias champion not found"),
        # uncaught by anything, landing as a 500 with no log line at all --
        # confirmed live: every real page load hit this (web/src/api/model.ts
        # calls it "the primary, lightweight ... endpoint" and throws on
        # !r.ok), and the operational counters had no ERROR entry to show
        # for it. 503, not 500: the code did what it was asked; the registry
        # entry it depends on does not exist yet.
        logger.warning("model/version: no registered model for the configured "
                        "alias yet: %s", e)
        raise HTTPException(
            status_code=503,
            detail="no model registered under the configured name/alias yet")
    except RegistryException as e:
        logger.warning("model/version: registry exception: %s", e)
        raise HTTPException(
            status_code=503,
            detail=e.detail)


@router.post("/model/reload", dependencies=[Depends(require_admin)])
def model_reload():
    try:
        reload_model()
        return {"status": "reloaded", "version": str(get_version())}
    except MlflowException as e:
        logger.warning("model/reload: no registered model for the configured "
                        "alias yet: %s", e)
        raise HTTPException(
            status_code=503,
            detail="no model registered under the configured name/alias yet")
    except RegistryException as e:
        logger.warning("model/reload: registry exception: %s", e)
        raise HTTPException(
            status_code=503,
            detail=e.detail)


@router.get("/model/calibration")
def model_calibration():
    import json, os
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(base, "output", "calibration_champion.json")
    if not os.path.exists(path):
        raise HTTPException(404, "run scripts/run_champion_calibration.py first")
    with open(path) as f:
        return json.load(f)


@router.get("/drift/features")
def drift_features(window_hours: int = 24):
    from app.drift.detector import report_features
    try:
        return report_features(window_hours=window_hours)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


@router.get("/drift/predictions")
def drift_predictions(window_hours: int = 24):
    from app.drift.detector import report_predictions
    try:
        return report_predictions(window_hours=window_hours)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
