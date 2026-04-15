import os
import mlflow
import mlflow.sklearn
from datetime import datetime

MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "sqlite:///mlflow.db")
EXPERIMENT_NAME = "sora-earth-esg"

mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)

try:
    mlflow.set_experiment(EXPERIMENT_NAME)
except Exception:
    pass


def _to_dict(input_data):
    if input_data is None:
        return {}
    if isinstance(input_data, dict):
        return input_data
    if hasattr(input_data, "model_dump"):
        return input_data.model_dump()
    if hasattr(input_data, "dict"):
        return input_data.dict()
    return {}


def _extract_probability(payload):
    if isinstance(payload, dict):
        for key in ("probability", "success_probability"):
            if payload.get(key) is not None:
                return payload.get(key)
    return None


def _extract_prediction(payload):
    if isinstance(payload, dict):
        return payload.get("prediction")
    return payload


def log_prediction(
    model_name: str,
    input_data,
    prediction=None,
    probability: float = None,
    probability_v2: float = None,
    latency_ms: float = None,
    confidence=None,
    esg_total_score: float = None,
):
    try:
        params = _to_dict(input_data)

        if isinstance(prediction, dict):
            payload = prediction
            pred_value = _extract_prediction(payload)
            prob_value = probability if probability is not None else _extract_probability(payload)
            prob_v2_value = probability_v2 if probability_v2 is not None else payload.get("probability_v2")
            conf_value = confidence if confidence is not None else payload.get("confidence")
            esg_value = esg_total_score if esg_total_score is not None else payload.get("total_score") or payload.get("esg_total_score")
            latency_value = latency_ms if latency_ms is not None else payload.get("latency_ms")
        else:
            pred_value = prediction
            prob_value = probability
            prob_v2_value = probability_v2
            conf_value = confidence
            esg_value = esg_total_score
            latency_value = latency_ms

        with mlflow.start_run(
            run_name=f"predict_{model_name}_{datetime.now().strftime('%H%M%S')}"
        ):
            if params:
                mlflow.log_params({k: str(v)[:250] for k, v in params.items()})

            metrics = {}
            if pred_value is not None:
                metrics["prediction"] = float(pred_value)
            if prob_value is not None:
                metrics["probability"] = float(prob_value)
            if prob_v2_value is not None:
                metrics["probability_v2"] = float(prob_v2_value)
                if prob_value is not None:
                    metrics["ab_divergence"] = abs(float(prob_value) - float(prob_v2_value))
            if latency_value is not None:
                metrics["latency_ms"] = float(latency_value)
            if esg_value is not None:
                metrics["esg_total_score"] = float(esg_value)

            if metrics:
                mlflow.log_metrics(metrics)

            mlflow.set_tag("model", model_name)
            mlflow.set_tag("type", "prediction")
            if conf_value is not None:
                mlflow.set_tag("confidence", str(conf_value))
    except Exception:
        pass


def log_evaluation(project_name: str, esg_scores: dict, risk_level: str):
    try:
        with mlflow.start_run(run_name=f"eval_{project_name}_{datetime.now().strftime('%H%M%S')}"):
            metrics = {
                "total_score": esg_scores.get("total_score", 0),
                "environment_score": esg_scores.get("environment_score", 0),
                "social_score": esg_scores.get("social_score", 0),
                "economic_score": esg_scores.get("economic_score", 0),
                "success_probability": esg_scores.get("success_probability", 0),
            }
            if esg_scores.get("success_probability_v2") is not None:
                metrics["success_probability_v2"] = esg_scores["success_probability_v2"]
                metrics["ab_divergence"] = abs(metrics["success_probability"] - metrics["success_probability_v2"])
            mlflow.log_metrics(metrics)
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
        result = {
            "experiment": EXPERIMENT_NAME,
            "total_runs": len(runs),
            "tracking_uri": MLFLOW_TRACKING_URI,
        }
        if not runs.empty:
            for key in ["rf_cv_auc", "xgb_cv_auc", "ensemble_cv_auc"]:
                col = f"metrics.{key}"
                if col in runs.columns:
                    vals = runs[col].dropna()
                    if not vals.empty:
                        result[key] = round(float(vals.iloc[0]), 4)
        return result
    except Exception as e:
        return {"status": "error", "detail": str(e)}
