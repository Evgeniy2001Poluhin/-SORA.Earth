from typing import List
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import csv, io, os
import numpy as np
import torch
from app.schemas import ProjectInput as Project
from app.validators import ProjectInput as LegacyProjectInput
from app.mlflow_tracking import log_prediction
from app.middleware import METRICS

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
    import app.main as m
    feats = m.make_features(_to_legacy(project))
    proba = float(m.rf_model.predict_proba(feats)[0][1])
    prediction = int(proba >= m.best_threshold)
    cat = getattr(project, "category", "Solar Energy")
    reg = getattr(project, "region", "Europe")
    prob_v2 = round(float(m.ensemble_model_v2.predict_proba(m.make_features_v2(_to_legacy(project), cat, reg))[0][1]) * 100, 2) if m.ensemble_model_v2 else round(proba*100, 2)
    result = {"prediction": prediction, "probability": round(proba*100,2), "probability_v2": prob_v2, "model": "RandomForest", "threshold": m.best_threshold}
    log_prediction("RandomForest", project.model_dump(), prediction, round(proba*100,2), prob_v2)
    METRICS["predictions_total"] = METRICS.get("predictions_total",0)+1
    return result

@router.post("/predict/neural")
def predict_neural(project: Project):
    from app.main import nn_model, best_threshold, make_features, make_features
    feats = make_features(_to_legacy(project))
    p = _nn_forward(nn_model, feats)
    prediction = int(p >= best_threshold)
    result = {"prediction": prediction, "probability": round(p*100,2), "model": "NeuralNet", "threshold": best_threshold}
    log_prediction("NeuralNet", project.model_dump(), prediction, round(p*100,2))
    METRICS["predictions_total"] = METRICS.get("predictions_total",0)+1
    return result

@router.post("/predict/stacking")
def predict_stacking(project: Project):
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
    METRICS["predictions_total"] = METRICS.get("predictions_total",0)+1
    return result

@router.post("/predict/compare")
def predict_compare(req: CompareRequest):
    results = []
    import app.main as m
    rf_model = m.rf_model
    xgb_model = m.xgb_model
    nn_model = m.nn_model
    ensemble_model = m.ensemble_model
    best_threshold = m.best_threshold
    for p in req.projects:
        feats = m.make_features(_to_legacy(p))
        rf_p = float(rf_model.predict_proba(feats)[0][1])
        xgb_p = float(xgb_model.predict_proba(feats)[0][1])
        nn_p = _nn_forward(nn_model, feats)
        ens_p = float(ensemble_model.predict_proba(feats)[0][1])
        prediction = int(ens_p >= best_threshold)
        results.append({"name": p.name, "prediction": prediction, "probability": round(ens_p*100,2), "base_models": {"rf": round(rf_p*100,2), "xgb": round(xgb_p*100,2), "nn": round(nn_p*100,2)}})
    results_sorted = sorted(results, key=lambda x: x["probability"], reverse=True)
    METRICS["predictions_total"] = METRICS.get("predictions_total",0)+len(req.projects)
    out = {"projects": results_sorted, "RandomForest": {"results": [{"name": r["name"], "probability": r["base_models"]["rf"]} for r in results_sorted]}, "XGBoost": {"results": [{"name": r["name"], "probability": r["base_models"]["xgb"]} for r in results_sorted]}, "NeuralNet": {"results": [{"name": r["name"], "probability": r["base_models"]["nn"]} for r in results_sorted]}, "StackingEnsemble": {"results": [{"name": r["name"], "probability": r["probability"]} for r in results_sorted]}}
    return out

@router.post("/shap")
def shap_explain(project: Project):
    from app.main import explainer_shap, make_features, make_features
    feats = make_features(_to_legacy(project))
    shap_values = explainer_shap.shap_values(feats)
    vals = shap_values[1][0].tolist() if isinstance(shap_values, list) else shap_values[0].tolist()
    feature_names = list(feats.columns)
    return {"shap_values": dict(zip(feature_names, vals)), "feature_names": feature_names}

@router.get("/predictions/history")
def predictions_history():
    from app.main import PRED_LOG, make_features
    if not PRED_LOG: raise HTTPException(status_code=500, detail="Prediction log path not configured")
    if not os.path.exists(PRED_LOG): return []
    with open(PRED_LOG,"r") as f: rows = list(csv.DictReader(f))
    return rows

@router.get("/predictions/export/csv")
def export_predictions_csv():
    from app.main import PRED_LOG, make_features
    if not PRED_LOG or not os.path.exists(PRED_LOG): raise HTTPException(status_code=404, detail="No prediction log found")
    with open(PRED_LOG,"r") as f: content = f.read()
    return StreamingResponse(io.BytesIO(content.encode()), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=sora_predictions_log.csv"})



@router.post("/predict/explain")
def predict_explain(project: Project):
    """Enhanced SHAP explanation: ranked features, verdict, confidence."""
    import app.main as m

    feats = m.make_features(_to_legacy(project))
    shap_values = m.explainer_shap.shap_values(feats)

    # Берём SHAP values для класса 1
    if isinstance(shap_values, list):
        raw = shap_values[1][0].ravel()
    else:
        raw = shap_values[0].ravel()

    # base_value для класса 1
    ev = m.explainer_shap.expected_value
    if hasattr(ev, "__len__"):
        base_value = float(ev[1])
    else:
        base_value = float(ev)

    proba = float(m.rf_model.predict_proba(feats)[0][1])
    prediction = int(proba >= m.best_threshold)

    feature_names = list(feats.columns)
    feature_values = feats.iloc[0].to_dict()

    contributions = []
    for shap_val, name, feat_val in zip(raw.tolist(), feature_names, feature_values.values()):
        contributions.append({
            "feature": name,
            "value": round(float(feat_val), 4),
            "shap": round(float(shap_val), 4),
            "direction": "positive" if shap_val > 0 else "negative",
            "impact": "high" if abs(shap_val) > 0.05 else "medium" if abs(shap_val) > 0.01 else "low"
        })

    contributions.sort(key=lambda x: abs(x["shap"]), reverse=True)
    top = contributions[0]

    _feat = top['feature']
    _shap = top['shap']
    _dir = 'boosting' if _shap > 0 else 'reducing'
    verdict = (
        f"{'Approved' if prediction else 'Rejected'}: "
        f"'{_feat}' is the key driver "
        f"({_dir} score by {abs(_shap):+.3f})"
    )

    return {
        "prediction": prediction,
        "probability": round(proba * 100, 2),
        "threshold": m.best_threshold,
        "verdict": verdict,
        "base_value": round(base_value, 4),
        "top_features": contributions[:3],
        "all_features": contributions,
    }
