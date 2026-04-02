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
    return LegacyProjectInput(budget=p.budget, co2_reduction=p.co2_reduction, social_impact=p.social_impact, duration_months=p.duration_months)

def _nn_forward(nn_model, feats):
    x = torch.tensor(feats.values, dtype=torch.float32)
    return float(nn_model(x).detach().numpy()[0][0])

@router.post("/predict")
def predict_project(project: Project):
    from app.main import rf_model, best_threshold, make_features
    feats = make_features(_to_legacy(project))
    proba = float(rf_model.predict_proba(feats)[0][1])
    prediction = int(proba >= best_threshold)
    result = {"prediction": prediction, "probability": round(proba*100,2), "model": "RandomForest", "threshold": best_threshold}
    prediction("RandomForest", project.model_dump(), prediction, round(proba*100,2))
    METRICS["predictions_total"] = METRICS.get("predictions_total",0)+1
    return result

@router.post("/predict/neural")
def predict_neural(project: Project):
    from app.main import nn_model, best_threshold, make_features
    feats = make_features(_to_legacy(project))
    p = _nn_forward(nn_model, feats)
    prediction = int(p >= best_threshold)
    result = {"prediction": prediction, "probability": round(p*100,2), "model": "NeuralNet", "threshold": best_threshold}
    log_prediction("NeuralNet", project.model_dump(), prediction, round(p*100,2))
    METRICS["predictions_total"] = METRICS.get("predictions_total",0)+1
    return result

@router.post("/predict/stacking")
def predict_stacking(project: Project):
    from app.main import rf_model, xgb_model, nn_model, ensemble_model, best_threshold, make_features
    feats = make_features(_to_legacy(project))
    rf_p = float(rf_model.predict_proba(feats)[0][1])
    xgb_p = float(xgb_model.predict_proba(feats)[0][1])
    nn_p = _nn_forward(nn_model, feats)
    ens_p = float(ensemble_model.predict_proba(feats)[0][1])
    prediction = int(ens_p >= best_threshold)
    result = {"prediction": prediction, "probability": round(ens_p*100,2), "base_models": {"rf": round(rf_p*100,2), "xgb": round(xgb_p*100,2), "nn": round(nn_p*100,2)}, "threshold": best_threshold, "model": "StackingEnsemble"}
    log_prediction("StackingEnsemble", project.model_dump(), prediction, round(ens_p*100,2))
    METRICS["predictions_total"] = METRICS.get("predictions_total",0)+1
    return result

@router.post("/predict/compare")
def predict_compare(req: CompareRequest):
    from app.main import rf_model, xgb_model, nn_model, ensemble_model, best_threshold, make_features
    results = []
    for p in req.projects:
        feats = make_features(_to_legacy(p))
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
    from app.main import explainer_shap, make_features
    feats = make_features(_to_legacy(project))
    shap_values = explainer_shap.shap_values(feats)
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
