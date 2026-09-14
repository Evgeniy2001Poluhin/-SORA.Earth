from typing import List
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
import csv, io, os, time
import numpy as np
import torch
from app.prom_metrics import sora_prediction_latency, sora_predictions_total

from app.schemas import NeuralNetworkUnavailable
from app.schemas import ProjectInput as Project
from app.validators import ProjectInput as LegacyProjectInput
from app.mlflow_tracking import log_prediction
from app.middleware import METRICS
from app.redis_cache import cache_get, cache_set

router = APIRouter()

def _cache_key(prefix: str, payload) -> str:
    return f"{prefix}:{hash(str(payload))}"



class CompareRequest(BaseModel):
    projects: List[Project]


def _to_legacy(p):
    return LegacyProjectInput(
        budget=p.budget,
        co2_reduction=p.co2_reduction,
        social_impact=p.social_impact,
        duration_months=p.duration_months,
    )


def _nn_forward(nn_model, feats):
    x = torch.tensor(feats.values, dtype=torch.float32)
    nn_model.eval()
    with torch.no_grad():
        return float(nn_model(x).cpu().numpy()[0][0])


def _base_probabilities(rf_model, xgb_model, nn_model, feats_9, feats_7):
    """The probability from each model that is loaded, keyed as `base_models`.

    The neural network is included only when its weights were loaded (#320). It
    used to be averaged in unconditionally, so on every environment without
    `pytorch_mlp.pth` a third of each blended probability came from a random
    network. The three routes that blend share this so none can drift back.
    """
    probabilities = {
        "rf": float(rf_model.predict_proba(feats_9)[0][1]),
        "xgb": float(xgb_model.predict_proba(feats_7)[0][1]),
    }
    if nn_model is not None:
        probabilities["nn"] = _nn_forward(nn_model, feats_9)
    return probabilities


def _blend(probabilities):
    return float(sum(probabilities.values()) / len(probabilities))


_NEURAL_UNAVAILABLE = NeuralNetworkUnavailable(
    reason_code="neural_network_unavailable",
    detail="The neural network has no loaded weights; no prediction was made.",
)


@router.post("/predict")
def predict_project(project: Project):
    import app.main as m

    ck = _cache_key("predict", project.model_dump())
    cached = cache_get(ck)
    if cached:
        cached["cached"] = True
        return cached

    start = time.perf_counter()
    feats = m.make_features_base(_to_legacy(project))
    proba = float(m.rf_model.predict_proba(feats)[0][1])
    prediction = int(proba >= m.best_threshold)

    cat = getattr(project, "category", "Solar Energy")
    reg = getattr(project, "region", "Europe")
    prob_v2 = (
        round(float(m.ensemble_model_v2.predict_proba(m.make_features_v2(_to_legacy(project), cat, reg))[0][1]) * 100, 2)
        if m.ensemble_model_v2 else round(proba * 100, 2)
    )

    prob_pct = round(proba * 100, 2)
    confidence = "high" if prob_pct >= 90 else "medium" if prob_pct >= 70 else "low"
    confidence_interval = [max(0.0, round(prob_pct - 5.0, 2)), min(100.0, round(prob_pct + 5.0, 2))]

    _lat = round((time.perf_counter() - start) * 1000, 2)
    sora_prediction_latency.observe(_lat)
    sora_predictions_total.labels(model="rf").inc()
    result = {
        "prediction": prediction,
        "probability": prob_pct,
        "probability_v2": prob_v2,
        "confidence": confidence,
        "confidence_interval": confidence_interval,
        "model": "RandomForest",
        "threshold": m.best_threshold,
        "inference_time_ms": _lat,
    }
    cache_set(ck, result)
    log_prediction("RandomForest", project.model_dump(), prediction, result["probability"])
    _log_csv(project)
    METRICS["predictions_total"] = METRICS.get("predictions_total", 0) + 1
    return result


@router.post(
    "/predict/neural",
    responses={
        503: {
            "model": NeuralNetworkUnavailable,
            "description": "The neural network has no loaded weights.",
        }
    },
)
def predict_neural(project: Project):
    from app.main import nn_model, best_threshold, make_features_base

    # Ahead of the cache, so the refusal cannot depend on what the cache holds.
    if nn_model is None:
        return JSONResponse(status_code=503, content=_NEURAL_UNAVAILABLE.model_dump())

    ck = _cache_key("neural", project.model_dump())
    cached = cache_get(ck)
    if cached:
        cached["cached"] = True
        return cached

    start = time.perf_counter()
    feats = make_features_base(_to_legacy(project))
    p = _nn_forward(nn_model, feats)
    prediction = int(p >= best_threshold)

    _lat = round((time.perf_counter() - start) * 1000, 2)
    sora_prediction_latency.observe(_lat)
    sora_predictions_total.labels(model="nn").inc()
    result = {
        "prediction": prediction,
        "probability": round(p * 100, 2),
        "model": "NeuralNet",
        "threshold": best_threshold,
        "inference_time_ms": _lat,
    }
    cache_set(ck, result)
    log_prediction("NeuralNet", project.model_dump(), prediction, result["probability"])
    METRICS["predictions_total"] = METRICS.get("predictions_total", 0) + 1
    return result


@router.post("/predict/stacking")
def predict_stacking(project: Project):
    import app.main as m

    # The composition is part of the key, so a result blended from a different
    # set of models is never served for this one.
    composition = "rf+xgb+nn" if m.nn_model is not None else "rf+xgb"
    ck = _cache_key("stacking:" + composition, project.model_dump())
    cached = cache_get(ck)
    if cached:
        cached["cached"] = True
        return cached

    start = time.perf_counter()

    feats_9 = m.make_features_base(_to_legacy(project))
    feats_7 = m.make_features_xgb(_to_legacy(project))

    probabilities = _base_probabilities(m.rf_model, m.xgb_model, m.nn_model, feats_9, feats_7)
    ens_p = _blend(probabilities)
    prediction = int(ens_p >= m.best_threshold)

    _lat = round((time.perf_counter() - start) * 1000, 2)
    sora_prediction_latency.observe(_lat)
    sora_predictions_total.labels(model="stacking").inc()
    result = {
        "prediction": prediction,
        "probability": round(ens_p * 100, 2),
        "base_models": {name: round(p * 100, 2) for name, p in probabilities.items()},
        "threshold": m.best_threshold,
        "model": "StackingEnsemble",
        "inference_time_ms": _lat,
    }
    cache_set(ck, result)
    log_prediction("StackingEnsemble", project.model_dump(), prediction, result["probability"])
    METRICS["predictions_total"] = METRICS.get("predictions_total", 0) + 1
    return result


@router.post("/predict/compare")
def predict_compare(req: CompareRequest):
    import app.main as m

    results = []
    for p in req.projects:
        feats_9 = m.make_features_base(_to_legacy(p))
        feats_7 = m.make_features_xgb(_to_legacy(p))

        probabilities = _base_probabilities(m.rf_model, m.xgb_model, m.nn_model, feats_9, feats_7)
        ens_p = _blend(probabilities)
        prediction = int(ens_p >= m.best_threshold)

        results.append({
            "name": p.name,
            "prediction": prediction,
            "probability": round(ens_p * 100, 2),
            "base_models": {name: round(v * 100, 2) for name, v in probabilities.items()},
        })

    results_sorted = sorted(results, key=lambda x: x["probability"], reverse=True)
    METRICS["predictions_total"] = METRICS.get("predictions_total", 0) + len(req.projects)

    def per_model(key):
        return {"results": [{"name": r["name"], "probability": r["base_models"][key]} for r in results_sorted]}

    response = {
        "projects": results_sorted,
        "RandomForest": per_model("rf"),
        "XGBoost": per_model("xgb"),
    }
    # Omitted, not nulled, when the network has no weights (#320, as #316).
    if m.nn_model is not None:
        response["NeuralNet"] = per_model("nn")
    response["StackingEnsemble"] = {
        "results": [{"name": r["name"], "probability": r["probability"]} for r in results_sorted]
    }
    return response


@router.post("/shap")
def shap_explain(project: Project):
    from app.main import explainer_shap, make_features_base

    feats = make_features_base(_to_legacy(project))
    shap_values = explainer_shap.shap_values(feats)
    raw = shap_values[1][0].tolist() if isinstance(shap_values, list) else shap_values[0].tolist()
    vals = [float(v[1]) if isinstance(v, (list, tuple)) else float(v) for v in raw]
    feature_names = list(feats.columns)
    return {"feature_names": feature_names, "shap_values": vals}


@router.get("/predictions/history")
def predictions_history():
    from app.main import PRED_LOG
    if not PRED_LOG:
        raise HTTPException(status_code=500, detail="Prediction log path not configured")
    if not os.path.exists(PRED_LOG):
        return []
    with open(PRED_LOG, "r") as f:
        rows = list(csv.DictReader(f))
    return rows


@router.get("/predictions/export/csv")
def export_predictions_csv():
    from app.main import PRED_LOG
    if not PRED_LOG or not os.path.exists(PRED_LOG):
        raise HTTPException(status_code=404, detail="No prediction log found")
    with open(PRED_LOG, "r") as f:
        content = f.read()
    return StreamingResponse(
        io.BytesIO(content.encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=sora_predictions_log.csv"},
    )


import os as _os, csv as _csv
from app.paths import data_dir as _data_dir
_PRED_LOG = _os.path.join(_data_dir(), 'predictions_log.csv')
def _log_csv(_project):
    _d = _project.model_dump()
    _row = {'budget': _d.get('budget_usd', _d.get('budget')), 'co2_reduction': _d.get('co2_reduction_tons_per_year', _d.get('co2_reduction')), 'social_impact': _d.get('social_impact_score', _d.get('social_impact')), 'duration_months': _d.get('project_duration_months', _d.get('duration_months'))}
    _new = not _os.path.exists(_PRED_LOG)
    _os.makedirs(_os.path.dirname(_PRED_LOG), exist_ok=True)
    with open(_PRED_LOG, 'a', newline='') as _f:
        _w = _csv.DictWriter(_f, fieldnames=list(_row))
        if _new: _w.writeheader()
        _w.writerow(_row)
