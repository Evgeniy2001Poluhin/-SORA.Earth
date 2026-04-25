"""Simple statistical drift detection via KS-test."""
import os
import pandas as pd
from fastapi import APIRouter
try:
    from scipy import stats as _stats
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

router = APIRouter(prefix="/model", tags=["mlops"])
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PRED_LOG = os.path.join(ROOT_DIR, "data", "predictions_log.csv")
PROJ_CSV = os.path.join(ROOT_DIR, "data", "projects.csv")
COLS = ["budget", "co2_reduction", "social_impact", "duration_months"]

@router.get("/drift")
def check_drift(window: int = 50):
    if not HAS_SCIPY:
        return {"status": "scipy_not_installed"}
    baseline = pd.read_csv(PROJ_CSV)
    if not os.path.exists(PRED_LOG):
        return {"status": "no_log", "drift": False}
    recent = pd.read_csv(PRED_LOG).tail(window)
    if len(recent) < 10:
        return {"status": "insufficient_data", "n": len(recent), "drift": False}
    results = {}
    drift_any = False
    for col in COLS:
        if col not in baseline.columns or col not in recent.columns:
            continue
        stat, p = _stats.ks_2samp(baseline[col].dropna().values, recent[col].dropna().values)
        d = bool(p < 0.05)
        if d: drift_any = True
        results[col] = {"ks_stat": round(float(stat),4), "p_value": round(float(p),4), "drift": d}
        # MLflow drift event logging (Sprint 7 closure)
    if drift_any:
        try:
            drifted = [c for c, m in results.items() if m.get("drift")]
            payload = {
                "drift_detected": True,
                "drift_score": round(len(drifted) / max(len(results), 1), 2),
                "drifted_features": drifted,
                "features_analyzed": list(results.keys()),
                "reference_samples": len(baseline) if baseline is not None else 0,
                "current_samples": len(recent) if recent is not None else 0,
                "ks_test": results,
                "psi": {},
            }
            from app.mlflow_tracking import log_drift_event as _lde
            _lde(payload, baseline_id="model_drift_endpoint")
        except Exception as _e:
            try:
                print("[mlflow_drift hook] failed:", _e)
            except Exception:
                pass
    return {"status": "ok", "drift_detected": drift_any, "window": window, "features": results}



@router.get("/drift/mlflow-history", tags=["mlops"])
def drift_mlflow_history(limit: int = 50):
    """Return recent drift events recorded in MLflow (tag type=drift_event)."""
    try:
        import mlflow as _ml
        runs = _ml.search_runs(
            filter_string="tags.type = 'drift_event'",
            order_by=["start_time DESC"],
            max_results=int(max(1, min(limit, 500))),
        )
    except Exception as e:
        return {"events": [], "count": 0, "error": str(e)}
    if runs is None or getattr(runs, "empty", True):
        return {"events": [], "count": 0}
    keep = [c for c in [
        "run_id", "start_time", "experiment_id",
        "metrics.drift_score", "metrics.drifted_features_count",
        "metrics.n_samples_ref", "metrics.n_samples_cur",
        "tags.baseline_id", "params.drifted_features",
    ] if c in runs.columns]
    df = runs[keep].copy()
    if "start_time" in df.columns:
        df["start_time"] = df["start_time"].astype(str)
    out = df.to_dict(orient="records")
    return {"events": out, "count": len(out)}
