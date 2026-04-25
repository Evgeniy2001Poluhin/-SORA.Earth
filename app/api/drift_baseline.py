from fastapi import APIRouter, HTTPException
import pandas as pd
import os
import random
from app.drift_detection import drift_detector

router = APIRouter()

NUM_FEATURES = ["budget","co2_reduction","social_impact","duration_months",
                "budget_per_month","co2_per_dollar","efficiency_score"]

@router.post("/mlops/drift/baseline/fit", tags=["mlops"])
def fit_baseline(csv_path: str = "data/projects.csv"):
    if not os.path.exists(csv_path):
        raise HTTPException(404, f"{csv_path} not found")
    df = pd.read_csv(csv_path)
    baseline = {}; used = []
    for c in NUM_FEATURES:
        if c in df.columns and pd.api.types.is_numeric_dtype(df[c]):
            v = df[c].dropna()
            if len(v) > 0:
                baseline[f"{c}_mean"] = float(v.mean())
                baseline[f"{c}_std"]  = float(v.std() or 1e-9)
                used.append(c)
    drift_detector.set_baseline(baseline)
    return {"status":"ok","samples":len(df),"features":used,"baseline_keys":sorted(baseline.keys())}

@router.post("/mlops/drift/observe", tags=["mlops"])
def observe(features: dict):
    drift_detector.add_observation(features)
    return {"status":"ok","observations":drift_detector.count()}

@router.delete("/mlops/drift/baseline", tags=["mlops"])
def reset_baseline():
    drift_detector.set_baseline({})
    drift_detector._observations = []
    return {"status":"reset"}

@router.post("/mlops/drift/simulate", tags=["mlops"])
def simulate_drift(shift: float = 5.0, n: int = 80):
    base = drift_detector.get_baseline()
    if not base:
        raise HTTPException(400, "fit baseline first")
    bm = base.get("budget_mean", 100000); bs = base.get("budget_std", 50000)
    drift_detector._observations = []
    for _ in range(n):
        drift_detector.add_observation({
            "budget": bm + shift*bs + random.gauss(0, bs*0.3),
            "co2_reduction": 200 + random.gauss(0, 50),
            "social_impact": 7 + random.gauss(0, 1),
            "duration_months": 18 + random.gauss(0, 3),
        })
    return {"status":"simulated","shift_sigma":shift,"observations":drift_detector.count()}
