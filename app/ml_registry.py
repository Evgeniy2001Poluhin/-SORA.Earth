"""SORA.Earth — MLflow Registry production model wrapper.

Загружает champion-модель из Registry, применяет препроцессинг
(feature engineering + scaler + cat encoding) и возвращает вероятность success.

Fallback: при недоступности Registry возвращает None — caller должен
свалиться на старый model.pkl через app.main.rf_model.
"""
import os
import json
import pickle
import logging
from typing import Optional, Dict, Any

import pandas as pd

logger = logging.getLogger(__name__)

FEATURES = [
    "budget", "co2_reduction", "social_impact", "duration_months",
    "budget_per_month", "co2_per_dollar", "efficiency_score",
    "impact_ratio", "budget_efficiency", "category_enc", "region_enc",
]

MODEL_NAME = "esg-success-predictor"
MODEL_ALIAS = "champion"

_model = None
_scaler = None
_encodings = None
_meta: Dict[str, Any] = {
    "name": MODEL_NAME,
    "error": None,
    "tracking_uri": None,
}


def _ensure_loaded() -> bool:
    """Lazy load model + scaler + encodings."""
    global _model, _scaler, _encodings
    if _model is not None:
        return True
    try:
        import mlflow
        uri = os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5556")
        mlflow.set_tracking_uri(uri)
        _meta["tracking_uri"] = uri

        _model = mlflow.sklearn.load_model(f"models:/{MODEL_NAME}@{MODEL_ALIAS}")

        scaler_path = os.path.join(os.path.dirname(__file__), "..", "models", "scaler_v2.pkl")
        with open(scaler_path, "rb") as f:
            _scaler = pickle.load(f)

        enc_path = os.path.join(os.path.dirname(__file__), "..", "models", "cat_encodings.json")
        with open(enc_path) as f:
            _encodings = json.load(f)

        _meta["loaded"] = True
        logger.info(f"[ml_registry] Loaded {MODEL_NAME}@{MODEL_ALIAS} from {uri}")
        return True
    except Exception as e:
        _meta["error"] = str(e)
        logger.warning(f"[ml_registry] Load failed, will fallback: {e}")
        _model = None
        return False


def _make_features(p: Dict[str, Any]) -> Dict[str, float]:
    """Feature engineering — соответствует train_model_v2.py:59-63."""
    budget = float(p.get("budget", 0))
    co2 = float(p.get("co2_reduction", 0))
    soc = float(p.get("social_impact", 0))
    dur = float(p.get("duration_months", 1))

    bpm = budget / max(dur, 1)
    return {
        "budget": budget,
        "co2_reduction": co2,
        "social_impact": soc,
        "duration_months": dur,
        "budget_per_month": bpm,
        "co2_per_dollar": co2 / max(budget, 1) * 1000,
        "efficiency_score": (co2 * soc) / max(dur, 1),
        "impact_ratio": soc / max(co2, 1),
        "budget_efficiency": co2 / max(bpm, 1),
        "category_enc": _encodings["category"].get(p.get("category", ""), 0.6),
        "region_enc": _encodings["region"].get(p.get("region", ""), 0.6),
    }


def predict_proba(project: Dict[str, Any]) -> Optional[float]:
    """Возвращает success_probability ∈ [0, 1] или None если Registry недоступен."""
    if not _ensure_loaded():
        return None
    try:
        row = _make_features(project)
        X_raw = pd.DataFrame([row])[FEATURES]
        X = _scaler.transform(X_raw)
        return float(_model.predict_proba(X)[0, 1])
    except Exception as e:
        logger.error(f"[ml_registry] predict failed: {e}")
        return None


def info() -> Dict[str, Any]:
    """Метаданные модели для /api/v1/model/registry-info."""
    _ensure_loaded()
    return dict(_meta)
