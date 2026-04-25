from fastapi import Depends
from app.auth import require_admin
"""Model retraining and metrics API."""
import os, csv, pickle, json, time
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score, classification_report
import torch

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from app.auth import require_api_key

router = APIRouter(prefix="/model", tags=["mlops"])

from app.scheduler import _start_retrain_log, _finish_retrain_log
logger = __import__("logging").getLogger("sora_earth")

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PRED_LOG = os.path.join(ROOT_DIR, "data", "predictions_log.csv")
PROJECTS_CSV = os.path.join(ROOT_DIR, "data", "projects.csv")
MODELS_DIR = os.path.join(ROOT_DIR, "models")



@router.get("/metrics")
def model_metrics():
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
    from app.main import best_threshold, model_meta
    from app.database import SessionLocal, RetrainLog
    import json

    db = SessionLocal()
    try:
        rows = (
            db.query(RetrainLog)
            .order_by(RetrainLog.started_at.desc())
            .limit(10)
            .all()
        )
        history = [
            {
                "status": r.status,
                "trigger_source": r.trigger_source,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "duration_sec": r.duration_sec,
                "model_version": r.model_version,
                "metrics": json.loads(r.metrics_json) if r.metrics_json else None,
            }
            for r in rows
        ]
    finally:
        db.close()

    return {
        "current_threshold": best_threshold,
        "meta": model_meta,
        "retrain_history": history,
        "prediction_log_size": _count_predictions(),
    }


def _get_current_metrics():
    """Return current model metrics from latest RetrainLog or MLflow."""
    try:
        from app.database import SessionLocal, RetrainLog
        from sqlalchemy import desc
        import json
        db = SessionLocal()
        try:
            row = db.query(RetrainLog).filter(
                RetrainLog.status == "success",
                RetrainLog.metrics_json.isnot(None),
            ).order_by(desc(RetrainLog.finished_at)).first()
            if row and row.metrics_json:
                return json.loads(row.metrics_json)
        finally:
            db.close()
    except Exception:
        pass
    return {}


def _do_retrain(min_samples: int = 50, trigger_source: str = "manual"):
    """Actual retrain logic with persisted RetrainLog."""
    log_id = _start_retrain_log(trigger_source=trigger_source, job_name="model_retrain")

    try:
        if not os.path.exists(PROJECTS_CSV):
            raise HTTPException(400, "No training data (projects.csv) found")

        df = pd.read_csv(PROJECTS_CSV)
        required = ["budget", "co2_reduction", "social_impact", "duration_months", "success"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise HTTPException(400, f"Missing columns in projects.csv: {missing}")

        if os.path.exists(PRED_LOG):
            try:
                log_df = pd.read_csv(PRED_LOG)
                enrichment_count = len(log_df) if len(log_df) > 0 and "prediction" in log_df.columns else 0
            except Exception:
                enrichment_count = 0
        else:
            enrichment_count = 0

        min_samples = max(10, min(min_samples, 100000))  # safety clamp
        if len(df) < min_samples:
            raise HTTPException(400, f"Need at least {min_samples} samples, have {len(df)}")

        from datetime import datetime as _dt
        df["budget_per_month"] = df["budget"] / df["duration_months"].clip(lower=1)
        df["co2_per_dollar"]   = df["co2_reduction"] / df["budget"].clip(lower=1) * 1000
        df["efficiency_score"] = (df["co2_reduction"] * df["social_impact"]) / df["duration_months"].clip(lower=1)
        df["year"]             = _dt.utcnow().year
        df["quarter"]          = (_dt.utcnow().month - 1) // 3 + 1

        feature_cols = ["budget", "co2_reduction", "social_impact", "duration_months",
                        "budget_per_month", "co2_per_dollar", "efficiency_score",
                        "year", "quarter"]

        X = df[feature_cols].values
        y = df["success"].values
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

        from sklearn.preprocessing import StandardScaler
        scaler = StandardScaler()
        X_train_s = scaler.fit_transform(X_train)
        X_test_s = scaler.transform(X_test)

        rf = RandomForestClassifier(n_estimators=200, max_depth=10, random_state=42, n_jobs=-1)
        rf.fit(X_train_s, y_train)

        from sklearn.calibration import CalibratedClassifierCV
        rf_cal = CalibratedClassifierCV(rf, cv="prefit", method="isotonic")
        rf_cal.fit(X_test_s, y_test)
        with open(os.path.join(MODELS_DIR, "rf_model_cal.pkl"), "wb") as fc:
            pickle.dump(rf_cal, fc)

        y_pred = rf.predict(X_test_s)
        y_proba = rf.predict_proba(X_test_s)[:, 1]

        acc = round(accuracy_score(y_test, y_pred), 4)
        f1 = round(f1_score(y_test, y_pred), 4)
        try:
            auc = round(roc_auc_score(y_test, y_proba), 4)
        except Exception:
            auc = None

        best_t, best_f1 = 0.5, f1
        for t in np.arange(0.3, 0.8, 0.01):
            preds_t = (y_proba >= t).astype(int)
            f1_t = f1_score(y_test, preds_t)
            if f1_t > best_f1:
                best_f1 = round(f1_t, 4)
                best_t = round(t, 2)

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

        try:
            from app import main as m
            m.rf_model = rf
            m.scaler = scaler
            m.best_threshold = best_t
            m.model_meta = new_meta
            m.model_metrics = new_metrics
            m.explainer_shap = __import__("shap").TreeExplainer(rf)
            reloaded = True
        except Exception:
            reloaded = False

        result = {
            "status": "success",
            "metrics": new_metrics,
            "meta": new_meta,
            "models_reloaded": reloaded,
            "timestamp": timestamp,
        }

        try:
            from app.mlflow_tracking import log_model_registry
            log_model_registry(rf, "RandomForest_retrain", {"auc": auc or 0, "f1": f1, "accuracy": acc})
        except Exception:
            pass


        _finish_retrain_log(
            log_id=log_id,
            status="success",
            message="Manual retraining completed successfully",
            model_version=timestamp,
            metrics=new_metrics,
        )
        return result

    except Exception as e:
        logger.exception("Retrain failed in _do_retrain: %s", e)
        _finish_retrain_log(
            log_id=log_id,
            status="failed",
            message="Manual retraining failed",
            error_message=str(e),
        )
        raise

@router.post("/retrain")
def retrain_model(background_tasks: BackgroundTasks, current_user=Depends(require_admin), min_samples: int = 50, sync: bool = False):
    """Retrain RF. Default=async, ?sync=true for synchronous."""
    if sync:
        return _do_retrain(min_samples)
    background_tasks.add_task(_do_retrain, min_samples)
    return {
        "status": "accepted",
        "message": "Retrain started in background",
        "check_status": "/model/status",
    }


@router.get("/feature-importance")
def feature_importance(current_user=Depends(require_api_key)):
    from app.main import rf_model, FEATURE_COLS
    importances = rf_model.feature_importances_
    pairs = sorted(zip(FEATURE_COLS, importances.tolist()), key=lambda x: -x[1])
    return {"features": [{"name": n, "importance": round(v, 4)} for n, v in pairs]}


@router.get("/prediction-log/stats")
def prediction_log_stats():
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


@router.post("/data/refresh")
def data_refresh(
    budget: float,
    co2_reduction: float,
    social_impact: float,
    duration_months: int,
    success: int,
    auto_retrain_threshold: int = 20, current_user=Depends(require_admin),
):
    """Append a labeled data point. Auto-retrain when threshold reached."""
    if success not in (0, 1):
        raise HTTPException(400, "success must be 0 or 1")

    new_row = {
        "budget": budget,
        "co2_reduction": co2_reduction,
        "social_impact": social_impact,
        "duration_months": duration_months,
        "success": success,
        "name": f"auto_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
        "category": "Unknown",
        "region": "Unknown",
    }

    df_existing = pd.read_csv(PROJECTS_CSV)
    df_new = pd.concat([df_existing, pd.DataFrame([new_row])], ignore_index=True)
    df_new.to_csv(PROJECTS_CSV, index=False)

    # Read last retrain total_samples from RetrainLog
    from app.database import SessionLocal, RetrainLog
    _db = SessionLocal()
    try:
        _last = _db.query(RetrainLog).filter(RetrainLog.status == 'success').order_by(RetrainLog.started_at.desc()).first()
        if _last and _last.metrics_json:
            _m = json.loads(_last.metrics_json)
            last_retrain_samples = _m.get('train_samples', 0) + _m.get('test_samples', 0)
        else:
            last_retrain_samples = 0
    finally:
        _db.close()
    new_since_retrain = len(df_new) - last_retrain_samples

    triggered = False
    retrain_result = None
    if new_since_retrain >= auto_retrain_threshold:
        try:
            retrain_result = _do_retrain(min_samples=50, trigger_source="auto")
            triggered = True
        except Exception as e:
            retrain_result = {"error": str(e)}

    return {
        "status": "added",
        "total_samples": len(df_new),
        "new_since_last_retrain": new_since_retrain,
        "auto_retrain_triggered": triggered,
        "retrain_result": retrain_result,
    }


@router.post("/data/bulk-upload")
def data_bulk_upload(file_path: str, auto_retrain: bool = False):
    """Upload CSV with columns: budget,co2_reduction,social_impact,duration_months,success"""
    if not os.path.exists(file_path):
        raise HTTPException(400, f"File not found: {file_path}")
    try:
        df_new = pd.read_csv(file_path)
    except Exception as e:
        raise HTTPException(400, f"CSV parse error: {e}")
    required = ["budget", "co2_reduction", "social_impact", "duration_months", "success"]
    missing = [c for c in required if c not in df_new.columns]
    if missing:
        raise HTTPException(400, f"Missing columns: {missing}")
    invalid = df_new[~df_new["success"].isin([0, 1])]
    if len(invalid) > 0:
        raise HTTPException(400, f"{len(invalid)} rows have invalid success values (must be 0 or 1)")
    df_existing = pd.read_csv(PROJECTS_CSV)
    df_merged = pd.concat([df_existing, df_new], ignore_index=True)
    df_merged.to_csv(PROJECTS_CSV, index=False)
    result = {
        "status": "uploaded",
        "rows_added": len(df_new),
        "total_samples": len(df_merged),
    }
    if auto_retrain:
        try:
            retrain_result = _do_retrain(min_samples=50, trigger_source="auto")
            result["retrain"] = retrain_result["metrics"]
        except Exception as e:
            result["retrain_error"] = str(e)
    return result