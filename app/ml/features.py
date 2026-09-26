"""Feature engineering aligned with train_model_v2.py (uses bundle's preprocessor)."""
import pandas as pd
from fastapi import HTTPException


class UnknownCategoryError(ValueError):
    """Raised when category or region is not in the model's vocabulary."""
    def __init__(self, field: str, value: str, known_values: list):
        self.field = field
        self.value = value
        self.known_values = known_values
        super().__init__(f"Unknown {field}: {value}")


def build_features(raw: dict, bundle: dict) -> pd.DataFrame:
    """Build features from raw input using the bundle's preprocessor.

    Feature formulas from train_model_v2.py lines 65-69.

    Args:
        raw: Input dict with budget, co2_reduction, social_impact, duration_months, category, region
        bundle: Dict with keys: features, cat_encodings, scaler

    Returns:
        Scaled feature DataFrame ready for model.predict_proba()

    Raises:
        UnknownCategoryError: If category or region is not in cat_encodings
    """
    from app.scales import social_impact_to_model

    cat_encodings = bundle["cat_encodings"]
    scaler = bundle["scaler"]
    features = bundle["features"]

    budget = float(raw["budget"])
    co2 = float(raw["co2_reduction"])
    # Convert API scale (0-10) to model scale (0-100) once
    social = social_impact_to_model(float(raw["social_impact"]))
    dur = max(1.0, float(raw["duration_months"]))

    # train_model_v2.py line 65: budget / duration_months.clip(lower=1)
    budget_per_month = budget / dur
    # train_model_v2.py line 66: co2_reduction / budget.clip(lower=1) * 1000
    co2_per_dollar = co2 / max(1.0, budget) * 1000.0
    # train_model_v2.py line 67: (co2_reduction * social_impact) / duration_months.clip(lower=1)
    efficiency_score = (co2 * social) / dur
    # train_model_v2.py line 68: social_impact / co2_reduction.clip(lower=1)
    impact_ratio = social / max(1.0, co2)
    # train_model_v2.py line 69: co2_reduction / budget_per_month.clip(lower=1)
    budget_efficiency = co2 / max(1.0, budget_per_month)

    category = raw.get("category", "energy")
    region = raw.get("region", "EU")

    # Target encoding from train_model_v2.py lines 72-75
    cat_enc = cat_encodings.get("category", {}).get(category)
    reg_enc = cat_encodings.get("region", {}).get(region)

    if cat_enc is None:
        known = sorted(cat_encodings.get("category", {}).keys())
        raise UnknownCategoryError("category", category, known)

    if reg_enc is None:
        known = sorted(cat_encodings.get("region", {}).keys())
        raise UnknownCategoryError("region", region, known)

    row = {
        "budget": budget,
        "co2_reduction": co2,
        "social_impact": social,
        "duration_months": dur,
        "budget_per_month": budget_per_month,
        "co2_per_dollar": co2_per_dollar,
        "efficiency_score": efficiency_score,
        "impact_ratio": impact_ratio,
        "budget_efficiency": budget_efficiency,
        "category_enc": float(cat_enc),
        "region_enc": float(reg_enc),
    }

    # Select columns in the bundle's feature order
    df = pd.DataFrame([row], columns=features).astype("float64")

    # Scale with the bundle's scaler (train_model_v2.py line 82-83)
    scaled = scaler.transform(df)
    return pd.DataFrame(scaled, columns=features).astype("float64")
