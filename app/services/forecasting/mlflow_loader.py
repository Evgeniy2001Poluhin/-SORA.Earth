"""MLflow model loader for production forecasting.

This module provides utilities to load trained forecast models from MLflow
Model Registry and use them for production predictions.
"""

import os
import logging
from typing import Optional, Dict
from pathlib import Path

log = logging.getLogger(__name__)

# Global model cache
_mlflow_models: Dict[str, any] = {}
_mlflow_available = False

try:
    import mlflow
    import mlflow.pytorch
    _mlflow_available = True
except ImportError:
    log.warning("MLflow not available. Production model loading disabled.")


def is_mlflow_available() -> bool:
    """Check if MLflow is available and configured."""
    return _mlflow_available and os.getenv("MLFLOW_TRACKING_URI") is not None


def load_production_model(metric: str, model_type: str = "lstm") -> Optional[any]:
    """Load a production model from MLflow Model Registry.

    Args:
        metric: Target metric (score, prob, co2)
        model_type: Model type (lstm, ensemble, prophet)

    Returns:
        Loaded model or None if not available
    """
    if not is_mlflow_available():
        log.debug("MLflow not available, skipping model load")
        return None

    cache_key = f"{model_type}-{metric}"

    # Check cache
    if cache_key in _mlflow_models:
        log.debug(f"Using cached MLflow model: {cache_key}")
        return _mlflow_models[cache_key]

    try:
        # Configure MLflow
        mlflow_uri = os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5556")
        mlflow.set_tracking_uri(mlflow_uri)

        # Model name in registry
        model_name = f"{model_type}-forecaster-{metric}"

        log.info(f"Loading model from MLflow: {model_name}")

        # Try to load latest version from Model Registry
        try:
            model_uri = f"models:/{model_name}/latest"
            model = mlflow.pytorch.load_model(model_uri)
            log.info(f"✓ Loaded MLflow model: {model_name} (latest)")
            _mlflow_models[cache_key] = model
            return model

        except Exception as e:
            log.warning(f"Could not load from Model Registry: {e}")

            # Fallback: try to load from recent runs
            from mlflow.tracking import MlflowClient
            client = MlflowClient()

            try:
                experiment = client.get_experiment_by_name("time-series-forecasting")
                if not experiment:
                    log.warning("Experiment 'time-series-forecasting' not found")
                    return None

                # Find recent run for this metric and model type
                runs = client.search_runs(
                    experiment_ids=[experiment.experiment_id],
                    filter_string=f"params.model_type = '{model_type.upper()}' AND params.metric LIKE '%{metric}%'",
                    order_by=["start_time DESC"],
                    max_results=1
                )

                if not runs:
                    log.warning(f"No runs found for {model_type} + {metric}")
                    return None

                run = runs[0]
                model_uri = f"runs:/{run.info.run_id}/{model_type}_model"
                model = mlflow.pytorch.load_model(model_uri)
                log.info(f"✓ Loaded MLflow model from run: {run.info.run_id}")
                _mlflow_models[cache_key] = model
                return model

            except Exception as e2:
                log.error(f"Could not load from runs: {e2}")
                return None

    except Exception as e:
        log.error(f"Failed to load MLflow model: {e}")
        return None


def clear_model_cache():
    """Clear the MLflow model cache."""
    global _mlflow_models
    _mlflow_models.clear()
    log.info("MLflow model cache cleared")


def get_model_info(metric: str, model_type: str = "lstm") -> Dict:
    """Get information about a production model.

    Args:
        metric: Target metric
        model_type: Model type

    Returns:
        Dict with model metadata
    """
    if not is_mlflow_available():
        return {"available": False, "reason": "MLflow not configured"}

    try:
        mlflow_uri = os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5556")
        mlflow.set_tracking_uri(mlflow_uri)

        from mlflow.tracking import MlflowClient
        client = MlflowClient()

        model_name = f"{model_type}-forecaster-{metric}"

        try:
            # Try Model Registry first
            versions = client.search_model_versions(f"name='{model_name}'")
            if versions:
                latest = max(versions, key=lambda v: int(v.version))
                return {
                    "available": True,
                    "source": "model_registry",
                    "name": model_name,
                    "version": latest.version,
                    "status": latest.status,
                    "run_id": latest.run_id
                }
        except Exception:
            pass

        # Fallback to recent runs
        experiment = client.get_experiment_by_name("time-series-forecasting")
        if experiment:
            runs = client.search_runs(
                experiment_ids=[experiment.experiment_id],
                filter_string=f"params.model_type = '{model_type.upper()}'",
                order_by=["start_time DESC"],
                max_results=1
            )

            if runs:
                run = runs[0]
                return {
                    "available": True,
                    "source": "experiment_run",
                    "run_id": run.info.run_id,
                    "run_name": run.data.tags.get("mlflow.runName", "N/A"),
                    "metrics": {
                        "mae": run.data.metrics.get("mae"),
                        "rmse": run.data.metrics.get("rmse"),
                        "mape": run.data.metrics.get("mape")
                    }
                }

        return {"available": False, "reason": "No models found"}

    except Exception as e:
        return {"available": False, "reason": str(e)}
