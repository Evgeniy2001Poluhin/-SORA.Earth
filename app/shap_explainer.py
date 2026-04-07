import shap
import numpy as np
import pandas as pd
import joblib
import os

MODEL_DIR = os.getenv("MODEL_DIR", "/app/models")

def explain_prediction(features: dict) -> dict:
    """Generate SHAP explanation for a single prediction."""
    model_path = os.path.join(MODEL_DIR, "stacking_model.pkl")
    if not os.path.exists(model_path):
        model_path = os.path.join(MODEL_DIR, "random_forest_model.pkl")

    model = joblib.load(model_path)

    feature_names = ["budget", "co2_reduction", "social_impact", "duration_months"]
    X = pd.DataFrame([features], columns=feature_names)

    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X)

    # For binary classification, shap_values may be a list [class_0, class_1]
    if isinstance(shap_values, list):
        sv = shap_values[1][0]  # class 1 (positive)
    else:
        sv = shap_values[0]

    base_value = explainer.expected_value
    if isinstance(base_value, (list, np.ndarray)):
        base_value = float(base_value[1])
    else:
        base_value = float(base_value)

    feature_contributions = {
        name: {
            "value": float(X[name].iloc[0]),
            "shap_value": round(float(sv[i]), 6),
            "direction": "positive" if sv[i] > 0 else "negative"
        }
        for i, name in enumerate(feature_names)
    }

    # Sort by absolute impact
    sorted_features = sorted(
        feature_contributions.items(),
        key=lambda x: abs(x[1]["shap_value"]),
        reverse=True
    )

    return {
        "base_value": round(base_value, 6),
        "feature_contributions": dict(sorted_features),
        "top_driver": sorted_features[0][0],
        "top_driver_impact": sorted_features[0][1]["shap_value"],
    }
