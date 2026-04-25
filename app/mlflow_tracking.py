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
    import sqlite3, json
    result = {
        "experiment": EXPERIMENT_NAME,
        "tracking_uri": MLFLOW_TRACKING_URI,
        "total_runs": 0,
    }
    try:
        con = sqlite3.connect("data/sora.db")
        row = con.execute(
            "SELECT metrics_json, model_version, started_at FROM retrain_log "
            "WHERE status='success' AND metrics_json IS NOT NULL "
            "AND metrics_json != '' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        con.close()
        if row and row[0]:
            m = json.loads(row[0])
            roc = m.get("roc_auc") or m.get("auc")
            result["roc_auc"] = roc
            result["ensemble_cv_auc"] = m.get("ensemble_cv_auc") or roc
            result["rf_cv_auc"] = m.get("rf_cv_auc") or roc
            result["xgb_cv_auc"] = m.get("xgb_cv_auc") or roc
            result["f1_score"] = m.get("f1_score") or m.get("f1")
            result["accuracy"] = m.get("accuracy")
            result["best_f1"] = m.get("best_f1")
            result["best_threshold"] = m.get("best_threshold")
            result["train_samples"] = m.get("train_samples")
            result["test_samples"] = m.get("test_samples")
            result["model_version"] = row[1] or ""
            result["last_retrain_at"] = str(row[2])
            result["_source"] = "retrain_log"
    except Exception as e:
        result["_sqlite_error"] = str(e)
    try:
        experiment = mlflow.get_experiment_by_name(EXPERIMENT_NAME)
        if experiment:
            runs = mlflow.search_runs(experiment_ids=[experiment.experiment_id], max_results=100)
            result["total_runs"] = len(runs)
    except Exception:
        pass
    return result





def log_drift_event(analysis_result, baseline_id="default"):
    """Log drift detection event to MLflow.

    Stores PSI/KS metrics per feature, drift_score, drifted features.
    Tag type=drift_event for filtering in /drift/mlflow-history.
    """
    if not analysis_result or not analysis_result.get("drift_detected"):
        return
    try:
        from datetime import datetime as _dt
        run_name = "drift_" + _dt.now().strftime("%Y%m%d_%H%M%S")
        with mlflow.start_run(run_name=run_name):
            mlflow.set_tag("type", "drift_event")
            mlflow.set_tag("baseline_id", str(baseline_id))

            metrics = {
                "drift_score": float(analysis_result.get("drift_score", 0.0) or 0.0),
                "drifted_features_count": float(len(analysis_result.get("drifted_features", []) or [])),
                "n_samples_ref": float(analysis_result.get("reference_samples", 0) or 0),
                "n_samples_cur": float(analysis_result.get("current_samples", 0) or 0),
            }
            psi = analysis_result.get("psi") or {}
            for feat, m in psi.items():
                if isinstance(m, dict) and m.get("psi") is not None:
                    safe = str(feat).replace(" ", "_")[:40]
                    metrics["psi_" + safe] = float(m["psi"])
            ks = analysis_result.get("ks_test") or analysis_result.get("ks") or {}
            for feat, m in ks.items():
                if isinstance(m, dict) and m.get("p_value") is not None:
                    safe = str(feat).replace(" ", "_")[:40]
                    metrics["ks_pvalue_" + safe] = float(m["p_value"])
            mlflow.log_metrics(metrics)

            drifted = analysis_result.get("drifted_features", []) or []
            mlflow.log_param("drifted_features", ",".join(str(x) for x in drifted)[:250])
            feats = analysis_result.get("features_analyzed", []) or []
            mlflow.log_param("features_analyzed", ",".join(str(x) for x in feats)[:250])
    except Exception as _e:
        try:
            print("[mlflow_drift] log failed:", _e)
        except Exception:
            pass
