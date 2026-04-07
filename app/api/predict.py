from typing import List
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import csv, io, os, hashlib, json as _json
import numpy as np
import torch
from app.schemas import ProjectInput as Project
from app.validators import ProjectInput as LegacyProjectInput
from app.mlflow_tracking import log_prediction
from app.middleware import METRICS
from app.redis_cache import cache_get, cache_set, REDIS_AVAILABLE


def _cache_key(prefix, data):
    raw = _json.dumps(data, sort_keys=True, default=str)
    return "sora:" + prefix + ":" + hashlib.md5(raw.encode()).hexdigest()


def _rf_confidence(rf_model, feats):
    X = feats.values if hasattr(feats, "values") else feats
    base_p = float(rf_model.predict_proba(X)[0][-1])
    rng = np.random.default_rng(42)
    samples = np.clip(rng.normal(base_p, 0.04, 500), 0.0, 1.0)
    ci_low = round(float(np.percentile(samples, 5)) * 100, 2)
    ci_high = round(float(np.percentile(samples, 95)) * 100, 2)
    spread = ci_high - ci_low
    label = "high" if spread < 10 else "medium" if spread < 25 else "low"
    return ci_low, ci_high, label


router = APIRouter()

class CompareRequest(BaseModel):
    projects: List[Project]

def _to_legacy(p):
    return LegacyProjectInput(budget=p.budget, co2_reduction=p.co2_reduction, social_impact=p.social_impact, duration_months=p.duration_months, category=getattr(p, "category", "Solar Energy"))

def _nn_forward(nn_model, feats):
    x = torch.tensor(feats.values, dtype=torch.float32)
    return float(nn_model(x).detach().numpy()[0][0])

@router.post("/predict")
def predict_project(project: Project):
    ck = _cache_key("predict", project.model_dump())
    cached = cache_get(ck)
    if cached:
        cached["cached"] = True
        return cached
    import app.main as m
    feats = m.make_features(_to_legacy(project))
    proba = float(m.rf_model.predict_proba(feats)[0][1])
    prediction = int(proba >= m.best_threshold)
    cat = getattr(project, "category", "Solar Energy")
    reg = getattr(project, "region", "Europe")
    prob_v2 = round(float(m.ensemble_model_v2.predict_proba(m.make_features_v2(_to_legacy(project), cat, reg))[0][1]) * 100, 2) if m.ensemble_model_v2 else round(proba*100, 2)
    _ci_low, _ci_high, _ci_label = _rf_confidence(m.rf_model, feats)
    result = {"prediction": prediction, "probability": round(proba*100,2), "probability_v2": prob_v2, "model": "RandomForest", "threshold": m.best_threshold, "confidence_interval": [_ci_low, _ci_high], "confidence": _ci_label}
    log_prediction("RandomForest", project.model_dump(), prediction, round(proba*100,2), prob_v2)
    cache_set(ck, result, ttl=300)
    METRICS["predictions_total"] = METRICS.get("predictions_total",0)+1
    return result

@router.post("/predict/neural")
def predict_neural(project: Project):
    ck = _cache_key("neural", project.model_dump())
    cached = cache_get(ck)
    if cached:
        cached["cached"] = True
        return cached
    from app.main import nn_model, best_threshold, make_features
    feats = make_features(_to_legacy(project))
    p = _nn_forward(nn_model, feats)
    prediction = int(p >= best_threshold)
    result = {"prediction": prediction, "probability": round(p*100,2), "model": "NeuralNet", "threshold": best_threshold}
    log_prediction("NeuralNet", project.model_dump(), prediction, round(p*100,2))
    cache_set(ck, result, ttl=300)
    METRICS["predictions_total"] = METRICS.get("predictions_total",0)+1
    return result

@router.post("/predict/stacking")
def predict_stacking(project: Project):
    ck = _cache_key("stacking", project.model_dump())
    cached = cache_get(ck)
    if cached:
        cached["cached"] = True
        return cached
    import app.main as m
    feats = m.make_features(_to_legacy(project))
    rf_p = float(m.rf_model.predict_proba(feats)[0][1])
    xgb_p = float(m.xgb_model.predict_proba(feats)[0][1])
    nn_p = _nn_forward(m.nn_model, feats)
    ens_p = float(m.ensemble_model.predict_proba(feats)[0][1])
    prediction = int(ens_p >= m.best_threshold)
    cat = getattr(project, "category", "Solar Energy")
    reg = getattr(project, "region", "Europe")
    prob_v2 = round(float(m.ensemble_model_v2.predict_proba(m.make_features_v2(_to_legacy(project), cat, reg))[0][1]) * 100, 2) if m.ensemble_model_v2 else round(ens_p*100, 2)
    result = {"prediction": prediction, "probability": round(ens_p*100,2), "probability_v2": prob_v2, "base_models": {"rf": round(rf_p*100,2), "xgb": round(xgb_p*100,2), "nn": round(nn_p*100,2)}, "threshold": m.best_threshold, "model": "StackingEnsemble"}
    log_prediction("StackingEnsemble", project.model_dump(), prediction, round(ens_p*100,2), prob_v2)
    cache_set(ck, result, ttl=300)
    METRICS["predictions_total"] = METRICS.get("predictions_total",0)+1
    return result

@router.post("/predict/compare")
def predict_compare(req: CompareRequest):
    results = []
    import app.main as m
    for p in req.projects:
        feats = m.make_features(_to_legacy(p))
        rf_p = float(m.rf_model.predict_proba(feats)[0][1])
        xgb_p = float(m.xgb_model.predict_proba(feats)[0][1])
        nn_p = _nn_forward(m.nn_model, feats)
        ens_p = float(m.ensemble_model.predict_proba(feats)[0][1])
        prediction = int(ens_p >= m.best_threshold)
        results.append({"name": p.name, "prediction": prediction, "probability": round(ens_p*100,2), "base_models": {"rf": round(rf_p*100,2), "xgb": round(xgb_p*100,2), "nn": round(nn_p*100,2)}})
    results_sorted = sorted(results, key=lambda x: x["probability"], reverse=True)
    METRICS["predictions_total"] = METRICS.get("predictions_total",0)+len(req.projects)
    out = {"projects": results_sorted, "RandomForest": {"results": [{"name": r["name"], "probability": r["base_models"]["rf"]} for r in results_sorted]}, "XGBoost": {"results": [{"name": r["name"], "probability": r["base_models"]["xgb"]} for r in results_sorted]}, "NeuralNet": {"results": [{"name": r["name"], "probability": r["base_models"]["nn"]} for r in results_sorted]}, "StackingEnsemble": {"results": [{"name": r["name"], "probability": r["probability"]} for r in results_sorted]}}
    return out

@router.post("/shap")
def shap_explain(project: Project):
    from app.main import explainer_shap, make_features
    feats = make_features(_to_legacy(project))
    feats_np = feats.values
    shap_values = explainer_shap.shap_values(feats_np)
    vals = shap_values[1][0].tolist() if isinstance(shap_values, list) else shap_values[0].tolist()
    feature_names = list(feats.columns)
    return {"shap_values": dict(zip(feature_names, vals)), "feature_names": feature_names}

@router.get("/predictions/history")
def predictions_history():
    from app.main import PRED_LOG
    if not PRED_LOG: raise HTTPException(status_code=500, detail="Prediction log path not configured")
    if not os.path.exists(PRED_LOG): return []
    with open(PRED_LOG,"r") as f: rows = list(csv.DictReader(f))
    return rows

@router.get("/predictions/export/csv")
def export_predictions_csv():
    from app.main import PRED_LOG
    if not PRED_LOG or not os.path.exists(PRED_LOG): raise HTTPException(status_code=404, detail="No prediction log found")
    with open(PRED_LOG,"r") as f: content = f.read()
    return StreamingResponse(io.BytesIO(content.encode()), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=sora_predictions_log.csv"})

@router.post("/predict/explain")
def predict_explain(project: Project):
    import app.main as m
    feats = m.make_features(_to_legacy(project))
    feats_np = feats.values
    shap_values = m.explainer_shap.shap_values(feats_np)
    if isinstance(shap_values, list):
        raw = shap_values[1][0].ravel()
    else:
        raw = shap_values[0].ravel()
    ev = m.explainer_shap.expected_value
    base_value = float(ev[1]) if hasattr(ev, "__len__") else float(ev)
    proba = float(m.rf_model.predict_proba(feats)[0][1])
    prediction = int(proba >= m.best_threshold)
    feature_names = list(feats.columns)
    feature_values = feats.iloc[0].to_dict()
    contributions = []
    for shap_val, name, feat_val in zip(raw.tolist(), feature_names, feature_values.values()):
        contributions.append({"feature": name, "value": round(float(feat_val), 4), "shap": round(float(shap_val), 4), "direction": "positive" if shap_val > 0 else "negative", "impact": "high" if abs(shap_val) > 0.05 else "medium" if abs(shap_val) > 0.01 else "low"})
    contributions.sort(key=lambda x: abs(x["shap"]), reverse=True)
    top = contributions[0]
    _dir = "boosting" if top["shap"] > 0 else "reducing"
    verdict = ("Approved" if prediction else "Rejected") + ": '" + top["feature"] + "' is the key driver (" + _dir + " score by " + str(round(abs(top["shap"]), 3)) + ")"
    _ci_low, _ci_high, _ci_label = _rf_confidence(m.rf_model, feats)
    return {"prediction": prediction, "probability": round(proba * 100, 2), "threshold": m.best_threshold, "confidence_interval": [_ci_low, _ci_high], "confidence": _ci_label, "verdict": verdict, "base_value": round(base_value, 4), "top_features": contributions[:3], "all_features": contributions}
