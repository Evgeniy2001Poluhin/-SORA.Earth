"""Lazy-cached loader for MLflow Registry model via alias."""
import os, threading, json, pickle, logging
import mlflow
import mlflow.sklearn
import mlflow.artifacts
from mlflow.tracking import MlflowClient
from mlflow.exceptions import MlflowException

logger = logging.getLogger(__name__)

MLFLOW_URI = os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5556")
MODEL_NAME = os.getenv("ESG_MODEL_NAME", "esg-success-predictor")
MODEL_ALIAS = os.getenv("ESG_MODEL_ALIAS", "champion")


class RegistryException(Exception):
    """Exception raised when model or preprocessing artifacts cannot be loaded."""
    def __init__(self, reason_code: str, detail: str):
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(detail)


_lock = threading.Lock()
_bundle = None


def _load():
    global _bundle
    if os.getenv("SORA_OFFLINE", "0") == "1":
        return None

    mlflow.set_tracking_uri(MLFLOW_URI)

    try:
        # Load the model from registry
        model = mlflow.sklearn.load_model(f"models:/{MODEL_NAME}@{MODEL_ALIAS}")

        # Get model version to retrieve run_id
        mv = MlflowClient().get_model_version_by_alias(MODEL_NAME, MODEL_ALIAS)
        version = mv.version
        run_id = mv.run_id

    except MlflowException as e:
        logger.warning("Failed to load model from registry: %s", e)
        raise RegistryException(
            reason_code="registry_unavailable",
            detail="The model registry is not available; no prediction was made."
        )
    except Exception as e:
        logger.warning("Unexpected error loading model from registry: %s", e)
        raise RegistryException(
            reason_code="registry_unavailable",
            detail="The model registry is not available; no prediction was made."
        )

    # Load preprocessing artifacts from the same run
    try:
        # Download features.json
        features_path = mlflow.artifacts.download_artifacts(
            artifact_uri=f"runs:/{run_id}/features.json"
        )
        with open(features_path) as f:
            features_data = json.load(f)
            features = features_data["features"]

        # Download cat_encodings.json
        cat_encodings_path = mlflow.artifacts.download_artifacts(
            artifact_uri=f"runs:/{run_id}/cat_encodings.json"
        )
        with open(cat_encodings_path) as f:
            cat_encodings = json.load(f)

        # Download preprocessor/scaler.pkl
        scaler_path = mlflow.artifacts.download_artifacts(
            artifact_uri=f"runs:/{run_id}/preprocessor/scaler.pkl"
        )
        with open(scaler_path, "rb") as f:
            scaler = pickle.load(f)

    except (FileNotFoundError, KeyError, json.JSONDecodeError, pickle.UnpicklingError) as e:
        logger.warning("Failed to load preprocessing artifacts: %s", e)
        raise RegistryException(
            reason_code="preprocessor_unavailable",
            detail="The model's preprocessing artifacts are not available; no prediction was made."
        )
    except Exception as e:
        logger.warning("Unexpected error loading preprocessing artifacts: %s", e)
        raise RegistryException(
            reason_code="preprocessor_unavailable",
            detail="The model's preprocessing artifacts are not available; no prediction was made."
        )

    _bundle = {
        "model": model,
        "features": features,
        "cat_encodings": cat_encodings,
        "scaler": scaler,
        "version": version,
    }


def get_bundle():
    """Get the full bundle: model + preprocessing artifacts."""
    global _bundle
    if _bundle is None:
        with _lock:
            if _bundle is None:
                _load()
    return _bundle


def get_model():
    bundle = get_bundle()
    return bundle["model"] if bundle else None


def get_version():
    bundle = get_bundle()
    return bundle["version"] if bundle else None


def get_alias() -> str:
    return MODEL_ALIAS


def reload():
    global _bundle
    with _lock:
        _bundle = None
    try:
        return get_bundle()
    except RegistryException:
        return None
