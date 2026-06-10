"""Lazy-cached loader for MLflow Registry model via alias."""
import os, threading
import mlflow
import mlflow.sklearn
from mlflow.tracking import MlflowClient

MLFLOW_URI = os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5556")
MODEL_NAME = os.getenv("ESG_MODEL_NAME", "esg-success-predictor")
MODEL_ALIAS = os.getenv("ESG_MODEL_ALIAS", "champion")

_lock = threading.Lock()
_model = None
_version = None


def _load():
    global _model, _version
    if os.getenv("SORA_OFFLINE", "0") == "1":
        return None
    mlflow.set_tracking_uri(MLFLOW_URI)
    _model = mlflow.sklearn.load_model(f"models:/{MODEL_NAME}@{MODEL_ALIAS}")
    mv = MlflowClient().get_model_version_by_alias(MODEL_NAME, MODEL_ALIAS)
    _version = mv.version


def get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                _load()
    return _model


def get_version():
    if _version is None:
        get_model()
    return _version


def get_alias() -> str:
    return MODEL_ALIAS


def reload():
    global _model, _version
    with _lock:
        _model = None
        _version = None
    return get_model()
