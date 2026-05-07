from fastapi import APIRouter, HTTPException, Query
import pandas as pd
import os
import random
from typing import Optional
from app.drift_detection import drift_detector

router = APIRouter()

NUM_FEATURES = ["budget", "co2_reduction", "social_impact", "duration_months",
                "budget_per_month", "co2_per_dollar", "efficiency_score"]


@router.post("/mlops/drift/baseline/fit", tags=["mlops"])
def fit_baseline(csv_path: str = "data/projects.csv"):
    if not os.path.exists(csv_path):
        raise HTTPException(404, f"{csv_path} not found")
    df = pd.read_csv(csv_path)
    baseline = {}
    used = []
    for c in NUM_FEATURES:
        if c in df.columns and pd.api.types.is_numeric_dtype(df[c]):
            v = df[c].dropna()
            if len(v) > 0:
                baseline[f"{c}_mean"] = float(v.mean())
                baseline[f"{c}_std"] = float(v.std() or 1e-9)
                used.append(c)
    drift_detector.set_baseline(baseline)
    drift_detector._baseline_n_samples = len(df)
    return {
        "status": "ok",
        "samples": len(df),
        "n_samples": len(df),
        "features": used,
        "baseline_keys": sorted(baseline.keys()),
    }


@router.post("/mlops/drift/observe", tags=["mlops"])
def observe(features: dict):
    drift_detector.add_observation(features)
    return {"status": "ok", "observations": drift_detector.count()}


@router.get("/mlops/drift/baseline", tags=["mlops"])
def baseline_status():
    base = drift_detector.get_baseline()
    fitted = bool(base)
    keys = sorted(base.keys()) if fitted else []
    n_features = len([k for k in keys if k.endswith("_mean")])
    n_samples_fit = getattr(drift_detector, "_baseline_n_samples", 0) if fitted else 0
    return {
        "fitted": fitted,
        "exists": fitted,
        "n_samples": n_samples_fit,
        "observations": drift_detector.count(),
        "n_features": n_features,
        "feature_count": n_features,
        "baseline_keys": keys,
    }


@router.delete("/mlops/drift/baseline", tags=["mlops"])
def reset_baseline():
    drift_detector.set_baseline({})
    drift_detector._observations = []
    drift_detector._baseline_n_samples = 0
    return {"status": "reset"}


def _gen_observation(base: dict, shifts: dict) -> dict:
    obs = {}
    for f in NUM_FEATURES:
        m = base.get(f"{f}_mean")
        s = base.get(f"{f}_std", 1.0) or 1.0
        if m is None:
            continue
        shift = shifts.get(f, 0.0)
        obs[f] = m + shift * s + random.gauss(0, s * 0.3)
    return obs


@router.post("/mlops/drift/simulate", tags=["mlops"])
def simulate_drift(
    mode: Optional[str] = Query(None, pattern="^(stable|drift|custom)$"),
    shift: float = 5.0,
    n: int = 80,
):
    base = drift_detector.get_baseline()
    if not base:
        raise HTTPException(400, "fit baseline first")

    if mode == "stable":
        shifts = {f: 0.0 for f in NUM_FEATURES}
        applied_shift = 0.0
    elif mode == "drift":
        shifts = {"budget": 5.0, "co2_reduction": -3.0, "social_impact": 3.0}
        applied_shift = 5.0
    else:
        shifts = {"budget": shift}
        applied_shift = shift

    drift_detector._observations = []
    for _ in range(n):
        drift_detector.add_observation(_gen_observation(base, shifts))

    return {
        "status": "simulated",
        "mode": mode or "custom",
        "shift_sigma": applied_shift,
        "shifts": shifts,
        "observations": drift_detector.count(),
    }
