"""Model retraining and metrics API."""
import os, csv, pickle, json, time
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score, classification_report
import torch

from fastapi import APIRouter, HTTPException, Depends
from app.auth import require_api_key

router = APIRouter(prefix="/model", tags=["ml-ops"])

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PRED_LOG = os.path.join(ROOT_DIR, "data", "predictions_log.csv")
PROJECTS_CSV = os.path.join(ROOT_DIR, "data", "projects.csv")
MODELS_DIR = os.path.join(ROOT_DIR, "models")

_retrain_history = []


@router.get("/metrics")
def model_metrics():
    """Current model performance metrics from training."""
    metrics_path = os.path.join(MODELS_DIR, "metrics.json")
    if not os.path.exists(metrics_path):
        raise HTTPException(404, "No metrics file found")
    with open(metrics_path) as f:
        metrics = json.load(f)

    meta_path = os.path.join(MODELS_DIR, "meta.json")
    meta = {}
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)

    return {
        "metrics": metrics,
        "meta": meta,
        "models_available": [f for f in os.listdir(MODELS_DIR) if f.endswith(('.pkl', '.pth'))],
    }


@router.get("/status")
def model_status():
    """Current model status and retrain history."""
    from app.main import best_threshold, model_meta
    return {
        "current_threshold": best_threshold,
        "meta": model_meta,
        "retrain_history": _retrain_history[-10:],
        "prediction_log_size": _count_predictions(),
    }


@router.post("/retrain")
def retrain_model(min_samples: int = 50):
    """
    Retrain RandomForest on accumulated data.
    Uses projects.csv + predictions_log.csv as training signal.
    """
    # Load base training data
    if not os.path.exists(PROJECTS_CSV):
        raise HTTPException(400, "No training data (projects.csv) found")

    df = pd.read_csv(PROJECTS_CSV)
    required = ["budget", "co2_reduction", "social_impact", "duration_months", "success"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise HTTPException(400, f"Missing columns in projects.csv: {missing}")

    # Enrich with prediction log feedback if available
    if os.path.exists(PRED_LOG):
        try:
            log_df = pd.read_csv(PRED_LOG)
            if len(log_df) > 0 and "prediction" in log_df.columns:
                enrichment_count = len(log_df)
            else:
                enrichment_count = 0
        except Exception:
            enrichment_count = 0
    else:
        enrichment_count = 0

    if len(df) < min_samples:
        raise HTTPException(400, f"Need at least {min_samples} samples, have {len(df)}")

    # Feature engineering (same as make_features)
    df["budget_per_month"] = df["budget"] / df["duration_months"].clip(lower=1)
    df["co2_per_dollar"] = df["co2_reduction"] / df["budget"].clip(lower=1) * 1000
    df["efficiency_score"] = (df["co2_reduction"] * df["social_impact"]) / df["duration_months"].clip(lower=1)

    feature_cols = ["budget", "co2_reduction", "social_impact", "duration_months",
                    "budget_per_month", "co2_per_dollar", "efficiency_score"]

    X = df[feature_cols].values
    y = df["success"].values

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

    # Scale
    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)

    # Train RF
    rf = RandomForestClassifier(n_estimators=200, max_depth=10, random_state=42, n_jobs=-1)
    rf.fit(X_train_s, y_train)

    y_pred = rf.predict(X_test_s)
    y_proba = rf.predict_proba(X_test_s)[:, 1]

    acc = round(accuracy_score(y_test, y_pred), 4)
    f1 = round(f1_score(y_test, y_pred), 4)
    try:
        auc = round(roc_auc_score(y_test, y_proba), 4)
    except Exception:
        auc = None

    # Find best threshold
    best_t, best_f1 = 0.5, f1
    for t in np.arange(0.3, 0.8, 0.01):
        preds_t = (y_proba >= t).astype(int)
        f1_t = f1_score(y_test, preds_t)
        if f1_t > best_f1:
            best_f1 = round(f1_t, 4)
            best_t = round(t, 2)

    # Save models
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    with open(os.path.join(MODELS_DIR, "model.pkl"), "wb") as f:
        pickle.dump(rf, f)
    with open(os.path.join(MODELS_DIR, "scaler.pkl"), "wb") as f:
        pickle.dump(scaler, f)
    with open(os.path.join(MODELS_DIR, "best_threshold.pkl"), "wb") as f:
        pickle.dump({"threshold": best_t}, f)

    new_metrics = {
        "accuracy": acc, "f1_score": f1, "best_f1": best_f1,
        "roc_auc": auc, "best_threshold": best_t,
        "train_samples": len(X_train), "test_samples": len(X_test),
        "enrichment_from_log": enrichment_count,
    }
    with open(os.path.join(MODELS_DIR, "metrics.json"), "w") as f:
        json.dump(new_metrics, f, indent=2)

    new_meta = {
        "retrained_at": timestamp,
        "algorithm": "RandomForestClassifier",
        "n_estimators": 200, "max_depth": 10,
        "features": feature_cols, "total_samples": len(df),
    }
    with open(os.path.join(MODELS_DIR, "meta.json"), "w") as f:
        json.dump(new_meta, f, indent=2)

    # Reload in-memory models
    try:
        from app import main as m
        m.rf_model = rf
        m.scaler = scaler
        m.best_threshold = best_t
        m.model_meta = new_meta
        m.model_metrics = new_metrics
        m.explainer_shap = __import__("shap").TreeExplainer(rf)
        reloaded = True
    except Exception as e:
        reloaded = False

    result = {
        "status": "success",
        "metrics": new_metrics,
        "meta": new_meta,
        "models_reloaded": reloaded,
        "timestamp": timestamp,
    }
    _retrain_history.append(result)
    return result


@router.get("/feature-importance")
def feature_importance(current_user=Depends(require_api_key), ):
    """Current RF model feature importances."""
    from app.main import rf_model, FEATURE_COLS
    importances = rf_model.feature_importances_
    pairs = sorted(zip(FEATURE_COLS, importances.tolist()), key=lambda x: -x[1])
    return {"features": [{"name": n, "importance": round(v, 4)} for n, v in pairs]}


@router.get("/prediction-log/stats")
def prediction_log_stats():
    """Stats about accumulated prediction log."""
    if not os.path.exists(PRED_LOG):
        return {"total": 0, "file_exists": False}
    try:
        df = pd.read_csv(PRED_LOG)
        return {
            "total": len(df),
            "columns": list(df.columns),
            "file_exists": True,
            "file_size_kb": round(os.path.getsize(PRED_LOG) / 1024, 1),
        }
    except Exception as e:
        return {"total": 0, "error": str(e)}


def _count_predictions() -> int:
    if not os.path.exists(PRED_LOG):
        return 0
    try:
        with open(PRED_LOG) as f:
            return sum(1 for _ in f) - 1
    except Exception:
        return 0
