"""Test that POST /api/v1/explain/local explains the exact vector the model serves.

Measured defect on main e6841e4 (reference project: budget 100000, co2_reduction
150, social_impact 7, duration_months 24):

1. `_engineer()` produces different features than `make_features_v2()`. Seven of
   twelve unscaled features differ: co2_per_dollar 1.5 vs 0.0015 (×1000 missing);
   efficiency_score 437.5 vs 8.5; impact_ratio 0.4667 vs 105.0; budget_efficiency
   0.036 vs 6.25; category_enc 0.5 vs 0.0; region_enc 0.5 vs 0.0;
   country_gdp_per_capita 51203.55 (Germany) vs 12720 (median).

2. `prediction_proba` computed on UNSCALED `df`, while SHAP and serving predict
   on the SCALED row. Measured: 0.9407 vs the serving probability 0.9204.
"""

from fastapi.testclient import TestClient
from app.main import app
import pytest

client = TestClient(app, raise_server_exceptions=False)

# Reference project from the defect description
REF_PROJECT = {
    "budget": 100000,
    "co2_reduction": 150,
    "social_impact": 7,
    "duration_months": 24,
}


def test_explain_local_reports_the_same_vector_as_serving():
    """(a) raw_value matches make_features_v2_raw for all twelve features."""
    from app.validators import ProjectInput
    from app.main import scaler_v2, FEATURE_COLS_V2

    # Get explanation with all 12 features
    resp = client.post("/api/v1/explain/local", json=REF_PROJECT, params={"top_n": 12})
    assert resp.status_code == 200
    data = resp.json()
    assert "top_contributions" in data
    assert len(data["top_contributions"]) == 12

    # Build the expected raw row. On main, make_features_v2_raw does not exist,
    # so we compute it by inverse-transforming the scaled output.
    pi = ProjectInput(**REF_PROJECT)
    try:
        from app.main import make_features_v2_raw
        expected_raw = make_features_v2_raw(pi)
    except ImportError:
        # Fallback for main: inverse-transform the scaled row
        from app.main import make_features_v2
        scaled_df = make_features_v2(pi)
        expected_raw = scaler_v2.inverse_transform(scaled_df)

    # Check each feature's raw_value
    contrib_by_feat = {c["feature"]: c for c in data["top_contributions"]}
    for i, feat in enumerate(FEATURE_COLS_V2):
        # expected_raw is either a DataFrame (from make_features_v2_raw) or a numpy array (from inverse_transform)
        if hasattr(expected_raw, "iloc"):
            expected = float(expected_raw.iloc[0, i])
        else:
            expected = float(expected_raw[0, i])
        actual = contrib_by_feat[feat]["raw_value"]
        assert abs(actual - expected) < 1e-6, \
            f"{feat}: expected raw {expected}, got {actual}"


def test_explain_local_reports_the_same_probability_as_serving():
    """(b) prediction_proba matches serving (ensemble_model_v2 on scaled input)."""
    from app.validators import ProjectInput
    from app.main import ensemble_model_v2, make_features_v2

    resp = client.post("/api/v1/explain/local", json=REF_PROJECT, params={"top_n": 12})
    assert resp.status_code == 200
    data = resp.json()

    # Expected: serving probability
    pi = ProjectInput(**REF_PROJECT)
    scaled_feats = make_features_v2(pi)
    expected_prob = float(ensemble_model_v2.predict_proba(scaled_feats)[0][1])

    actual_prob = data["prediction_proba"]
    assert abs(actual_prob - expected_prob) < 1e-9, \
        f"Expected {expected_prob}, got {actual_prob}"


def test_explain_local_shap_additivity():
    """(c) base_value + sum(shap) = prediction_proba (Kernel SHAP invariant)."""
    resp = client.post("/api/v1/explain/local", json=REF_PROJECT, params={"top_n": 12, "nsamples": 100})
    assert resp.status_code == 200
    data = resp.json()

    base = data["base_value"]
    shap_sum = sum(c["shap_value"] for c in data["top_contributions"])
    pred = data["prediction_proba"]

    # The explainer should be using predict_proba on scaled input (class 1)
    assert abs(base + shap_sum - pred) < 1e-3, \
        f"Additivity violated: {base} + {shap_sum} = {base + shap_sum}, pred = {pred}"


def test_explain_local_rejects_missing_base_feature():
    """(d) Missing a base feature → 422 naming it."""
    incomplete = {k: v for k, v in REF_PROJECT.items() if k != "duration_months"}
    resp = client.post("/api/v1/explain/local", json=incomplete)
    assert resp.status_code == 422
    body = resp.text.lower()
    assert "duration_months" in body, f"Expected 'duration_months' in error, got: {resp.text}"


def test_explain_local_ignores_extra_features():
    """(e) Extra key → 200 with ignored_features, same explanation as without."""
    # Request with an extra key
    with_extra = {**REF_PROJECT, "category_enc": 3.0}
    resp_extra = client.post("/api/v1/explain/local", json=with_extra, params={"top_n": 12})
    assert resp_extra.status_code == 200
    data_extra = resp_extra.json()
    assert "ignored_features" in data_extra
    assert data_extra["ignored_features"] == ["category_enc"]

    # Request without the extra key
    resp_normal = client.post("/api/v1/explain/local", json=REF_PROJECT, params={"top_n": 12})
    assert resp_normal.status_code == 200
    data_normal = resp_normal.json()
    assert data_normal.get("ignored_features", []) == []

    # Prediction should be identical
    assert data_extra["prediction_proba"] == data_normal["prediction_proba"]
