"""Simple statistical drift detection via KS-test."""
import os
import pandas as pd
from fastapi import APIRouter
try:
    from scipy import stats as _stats
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

router = APIRouter(prefix="/model", tags=["ml-ops"])
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
    return {"status": "ok", "drift_detected": drift_any, "window": window, "features": results}
