"""Lazy-cached loader for MLflow Registry model (sklearn flavor)."""
import os, threading, mlflow
import mlflow.sklearn

MLFLOW_URI = os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5556")
MODEL_NAME = os.getenv("ESG_MODEL_NAME", "esg-success-predictor")
MODEL_STAGE = os.getenv("ESG_MODEL_STAGE", "Production")

_lock = threading.Lock()
_model = None
_version = None


def _load():
    global _model, _version
    mlflow.set_tracking_uri(MLFLOW_URI)
    uri = f"models:/{MODEL_NAME}/{MODEL_STAGE}"
    _model = mlflow.sklearn.load_model(uri)
    from mlflow.tracking import MlflowClient
    c = MlflowClient()
    mvs = c.get_latest_versions(MODEL_NAME, stages=[MODEL_STAGE])
    _version = mvs[0].version if mvs else "?"


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


def reload():
    global _model, _version
    with _lock:
        _model = None
        _version = None
    return get_model()
