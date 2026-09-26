"""Social impact scale conversion: API 0-10, models 0-100.

The interface (sliders 1-10, presets 7-9), the legacy validator
app/validators.py::ProjectInput (0-10) and the ESG formula in
app/main.py::calculate_esg (social_impact / 10.0) all use the 0-10 scale.

The trained models (RandomForest, XGBoost, Ensemble v2) were all fitted on
data/projects.csv, where social_impact is on the 0-100 scale (median 64;
models/scaler.pkl mean 61.13; models/scaler_v2.pkl mean 63.87, σ 8.39).

Conversion happens at the model boundary: model-feature builders receive API-scale
values and convert them to model-scale. Each builder converts once and uses the
model-scale value for both the raw social_impact column AND every derived feature
that uses social_impact (efficiency_score, impact_per_month, impact_ratio, ...).

Training data (data/projects.csv, bulk uploads, POST /data/refresh) is already on
the model scale (0-100), and nothing validates that yet -- out of scope for this fix.
"""

SOCIAL_IMPACT_MODEL_FACTOR = 10.0


def social_impact_to_model(value: float) -> float:
    """Convert API scale (0-10) to model scale (0-100)."""
    return float(value) * SOCIAL_IMPACT_MODEL_FACTOR


def social_impact_from_training(value: float) -> float:
    """Convert training row's model scale (0-100) to API scale (0-10)."""
    return float(value) / SOCIAL_IMPACT_MODEL_FACTOR
