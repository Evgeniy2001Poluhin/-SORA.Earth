import os
import mlflow
import mlflow.sklearn
import json
from datetime import datetime

MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "sqlite:///mlflow.db")
EXPERIMENT_NAME = "sora-earth-esg"

mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)

try:
    mlflow.set_experiment(EXPERIMENT_NAME)
except Exception:
    pass


def log_prediction(model_name: str, input_data: dict, prediction: float, probability: float):
    try:
        with mlflow.start_run(run_name=f"predict_{model_name}_{datetime.now().strftime('%H%M%S')}"):
            mlflow.log_params({k: str(v)[:250] for k, v in input_data.items()})
            mlflow.log_metrics({
                "prediction": prediction,
                "probability": probability,
            })
            mlflow.set_tag("model", model_name)
            mlflow.set_tag("type", "prediction")
    except Exception:
        pass


def log_evaluation(project_name: str, esg_scores: dict, risk_level: str):
    try:
        with mlflow.start_run(run_name=f"eval_{project_name}_{datetime.now().strftime('%H%M%S')}"):
            mlflow.log_metrics({
                "total_score": esg_scores.get("total_score", 0),
                "environment_score": esg_scores.get("environment_score", 0),
                "social_score": esg_scores.get("social_score", 0),
                "economic_score": esg_scores.get("economic_score", 0),
                "success_probability": esg_scores.get("success_probability", 0),
            })
            mlflow.set_tag("project", project_name)
            mlflow.set_tag("risk_level", risk_level)
            mlflow.set_tag("type", "evaluation")
    except Exception:
        pass


def log_model_registry(model, model_name: str, metrics: dict):
    try:
        with mlflow.start_run(run_name=f"register_{model_name}"):
            mlflow.log_metrics(metrics)
            mlflow.sklearn.log_model(model, model_name)
            mlflow.set_tag("type", "model_registry")
    except Exception:
        pass


def get_experiment_stats():
    try:
        experiment = mlflow.get_experiment_by_name(EXPERIMENT_NAME)
        if not experiment:
            return {"status": "no experiment found"}
        runs = mlflow.search_runs(experiment_ids=[experiment.experiment_id], max_results=100)
        return {
            "experiment": EXPERIMENT_NAME,
            "total_runs": len(runs),
            "tracking_uri": MLFLOW_TRACKING_URI,
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)}
