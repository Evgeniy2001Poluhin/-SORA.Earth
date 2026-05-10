"""Feature engineering aligned with train_model_v2.py (includes scaler)."""
import json, os, pickle
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CAT_PATH = os.path.join(ROOT, "models", "cat_encodings.json")
SCALER_PATH = os.path.join(ROOT, "models", "scaler_v2.pkl")

_cat = None
_scaler = None

def _load_cat():
    global _cat
    if _cat is None:
        with open(CAT_PATH) as f:
            _cat = json.load(f)
    return _cat

def _load_scaler():
    global _scaler
    if _scaler is None:
        with open(SCALER_PATH, "rb") as f:
            _scaler = pickle.load(f)
    return _scaler

FEATURES = [
    "budget", "co2_reduction", "social_impact", "duration_months",
    "budget_per_month", "co2_per_dollar", "efficiency_score",
    "impact_ratio", "budget_efficiency",
    "category_enc", "region_enc",
]


def build_features(raw: dict) -> pd.DataFrame:
    cat = _load_cat()

    budget = float(raw["budget"])
    co2 = float(raw["co2_reduction"])
    social = float(raw["social_impact"])
    dur = max(1.0, float(raw["duration_months"]))

    budget_per_month = budget / dur
    co2_per_dollar = co2 / max(1.0, budget) * 1000.0
    efficiency_score = (co2 * social) / dur
    impact_ratio = social / max(1.0, co2)
    budget_efficiency = co2 / max(1.0, budget_per_month)

    category = raw.get("category", "energy")
    region = raw.get("region", "EU")
    cat_enc = cat.get("category", {}).get(category)
    reg_enc = cat.get("region", {}).get(region)

    if cat_enc is None:
        vals = list(cat.get("category", {}).values())
        cat_enc = sum(vals) / len(vals) if vals else 0.5
    if reg_enc is None:
        vals = list(cat.get("region", {}).values())
        reg_enc = sum(vals) / len(vals) if vals else 0.5

    row = {
        "budget": budget, "co2_reduction": co2, "social_impact": social,
        "duration_months": dur,
        "budget_per_month": budget_per_month, "co2_per_dollar": co2_per_dollar,
        "efficiency_score": efficiency_score, "impact_ratio": impact_ratio,
        "budget_efficiency": budget_efficiency,
        "category_enc": float(cat_enc), "region_enc": float(reg_enc),
    }
    df = pd.DataFrame([row], columns=FEATURES).astype("float64")

    # CRITICAL: model was trained on scaled features
    scaler = _load_scaler()
    scaled = scaler.transform(df)
    return pd.DataFrame(scaled, columns=FEATURES).astype("float64")
