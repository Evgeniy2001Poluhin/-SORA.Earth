"""FastAPI routes powered by MLflow Registry model."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from mlflow.exceptions import MlflowException
import logging
import time

from app.obs.request_log import log_prediction

from app.ml.registry_loader import get_model, get_version, get_alias, reload as reload_model
from app.ml.features import build_features

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v2", tags=["ml-v2"])


class PredictRequest(BaseModel):
    budget: float = Field(..., gt=0)
    co2_reduction: float = Field(..., ge=0)
    social_impact: float = Field(..., ge=0, le=100)
    duration_months: int = Field(..., ge=1, le=120)
    category: str = "energy"
    region: str = "EU"


class PredictResponse(BaseModel):
    success_probability: float
    success_class: int
    model_version: str
    model_name: str = "esg-success-predictor"
    alias: str = "champion"


@router.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    try:
        t0 = time.perf_counter()
        X = build_features(req.dict())
        model = get_model()
        proba = float(model.predict_proba(X)[0][1])
        cls = int(round(proba))
        latency_ms = (time.perf_counter() - t0) * 1000.0
        mv = str(get_version())
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


@router.post("/model/reload")
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
