"""Social impact unified on 0-10 scale: dead zone elimination and validator consistency.

Measured defect (on main 76531a6, 2026-09-26):
- POST /api/v1/evaluate success_probability was 27.0 for every social_impact 1..20
  (the model's dead zone, because values 1-10 were not multiplied to the 0-100
  scale the model was trained on).
- Nine routes answered 500 for social_impact 70 (validator refusal, because the
  request schema limited to 10).
- GET /api/v1/explain/beeswarm answered 500 "No valid samples" (training rows are
  0-100 and failed the 0-10 validator).
- GET /api/v1/model/ab-comparison reported n_samples: 82 (only rows ≤10 passed
  the validator; 171 total rows in data/projects.csv).

Decision (owner, 2026-09-25):
social_impact is on 0–10 everywhere a client or the interface speaks, while the
trained models were fitted on 0–100 (data/projects.csv). app/scales.py holds the
constant (×10) and two helpers. Model-feature builders multiply by 10. Paths
reading training rows divide by 10 before the validator.
"""
from fastapi.testclient import TestClient
import pytest


# Base project used throughout
BASE_PROJECT = {
    "budget": 100000,
    "co2_reduction": 150,
    "duration_months": 24,
    "country": "Germany"
}


def _make_client():
    """Build TestClient with raise_server_exceptions=False to see 500s as status codes."""
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


# ============================================================================
# (a) Dead zone gone: social_impact 1 and 10 give different probabilities
# ============================================================================

def test_evaluate_dead_zone_gone():
    """POST /api/v1/evaluate: social_impact 1 vs 10 give different success_probability."""
    c = _make_client()
    
    p1 = {**BASE_PROJECT, "social_impact": 1}
    p10 = {**BASE_PROJECT, "social_impact": 10}
    
    r1 = c.post("/api/v1/evaluate", json=p1)
    r10 = c.post("/api/v1/evaluate", json=p10)
    
    assert r1.status_code == 200, f"social_impact=1 failed: {r1.text}"
    assert r10.status_code == 200, f"social_impact=10 failed: {r10.text}"
    
    data1 = r1.json()
    data10 = r10.json()
    
    assert "success_probability" in data1
    assert "success_probability" in data10
    
    prob1 = data1["success_probability"]
    prob10 = data10["success_probability"]
    
    assert prob1 != prob10, f"Dead zone: both returned {prob1}"


def test_predict_dead_zone_gone():
    """POST /api/v1/predict: social_impact 1 vs 10 give different probability."""
    c = _make_client()
    
    p1 = {**BASE_PROJECT, "social_impact": 1}
    p10 = {**BASE_PROJECT, "social_impact": 10}
    
    r1 = c.post("/api/v1/predict", json=p1)
    r10 = c.post("/api/v1/predict", json=p10)
    
    assert r1.status_code == 200
    assert r10.status_code == 200
    
    data1 = r1.json()
    data10 = r10.json()
    
    assert "probability" in data1
    assert "probability" in data10
    
    assert data1["probability"] != data10["probability"]


def test_stacking_dead_zone_gone():
    """POST /api/v1/predict/stacking: social_impact 1 vs 10 give different probability."""
    c = _make_client()
    
    p1 = {**BASE_PROJECT, "social_impact": 1}
    p10 = {**BASE_PROJECT, "social_impact": 10}
    
    r1 = c.post("/api/v1/predict/stacking", json=p1)
    r10 = c.post("/api/v1/predict/stacking", json=p10)
    
    assert r1.status_code == 200
    assert r10.status_code == 200
    
    data1 = r1.json()
    data10 = r10.json()
    
    assert "probability" in data1
    assert "probability" in data10
    
    assert data1["probability"] != data10["probability"]


def test_uncertainty_dead_zone_gone():
    """POST /api/v1/predict/uncertainty: social_impact 1 vs 10 give different probability."""
    c = _make_client()
    
    p1 = {**BASE_PROJECT, "social_impact": 1}
    p10 = {**BASE_PROJECT, "social_impact": 10}
    
    r1 = c.post("/api/v1/predict/uncertainty", json=p1)
    r10 = c.post("/api/v1/predict/uncertainty", json=p10)
    
    assert r1.status_code == 200
    assert r10.status_code == 200
    
    data1 = r1.json()
    data10 = r10.json()
    
    assert "probability" in data1
    assert "probability" in data10
    
    assert data1["probability"] != data10["probability"]


def test_explain_dead_zone_gone():
    """POST /api/v1/predict/explain: social_impact 1 vs 10 give different probability."""
    c = _make_client()
    
    p1 = {**BASE_PROJECT, "social_impact": 1}
    p10 = {**BASE_PROJECT, "social_impact": 10}
    
    r1 = c.post("/api/v1/predict/explain", json=p1)
    r10 = c.post("/api/v1/predict/explain", json=p10)
    
    assert r1.status_code == 200
    assert r10.status_code == 200
    
    data1 = r1.json()
    data10 = r10.json()
    
    assert "probability" in data1
    assert "probability" in data10
    
    assert data1["probability"] != data10["probability"]


def test_discrepancy_dead_zone_gone():
    """POST /api/v1/calibration/discrepancy: social_impact 1 vs 10 give different probabilities."""
    c = _make_client()
    
    p1 = {**BASE_PROJECT, "social_impact": 1}
    p10 = {**BASE_PROJECT, "social_impact": 10}
    
    r1 = c.post("/api/v1/calibration/discrepancy", json=p1)
    r10 = c.post("/api/v1/calibration/discrepancy", json=p10)
    
    assert r1.status_code == 200
    assert r10.status_code == 200
    
    data1 = r1.json()
    data10 = r10.json()
    
    assert "models" in data1
    assert "models" in data10
    assert "rf_v1" in data1["models"]
    assert "rf_v1" in data10["models"]
    assert "proba" in data1["models"]["rf_v1"]
    assert "proba" in data10["models"]["rf_v1"]
    
    prob1 = data1["models"]["rf_v1"]["proba"]
    prob10 = data10["models"]["rf_v1"]["proba"]
    
    assert prob1 != prob10


def test_compare_dead_zone_gone():
    """POST /api/v1/predict/compare: social_impact 1 vs 10 give different probabilities."""
    c = _make_client()
    
    p1 = {**BASE_PROJECT, "social_impact": 1}
    p10 = {**BASE_PROJECT, "social_impact": 10}
    
    body1 = {"projects": [p1]}
    body10 = {"projects": [p10]}
    
    r1 = c.post("/api/v1/predict/compare", json=body1)
    r10 = c.post("/api/v1/predict/compare", json=body10)
    
    assert r1.status_code == 200
    assert r10.status_code == 200
    
    data1 = r1.json()
    data10 = r10.json()
    
    assert "projects" in data1
    assert "projects" in data10
    assert len(data1["projects"]) > 0
    assert len(data10["projects"]) > 0
    assert "probability" in data1["projects"][0]
    assert "probability" in data10["projects"][0]
    
    prob1 = data1["projects"][0]["probability"]
    prob10 = data10["projects"][0]["probability"]
    
    assert prob1 != prob10


def test_analytics_model_compare_dead_zone_gone():
    """POST /api/v1/analytics/model-compare: social_impact 1 vs 10 give different probabilities."""
    c = _make_client()
    
    p1 = {**BASE_PROJECT, "social_impact": 1}
    p10 = {**BASE_PROJECT, "social_impact": 10}
    
    r1 = c.post("/api/v1/analytics/model-compare", json=p1)
    r10 = c.post("/api/v1/analytics/model-compare", json=p10)
    
    assert r1.status_code == 200
    assert r10.status_code == 200
    
    data1 = r1.json()
    data10 = r10.json()
    
    assert "models" in data1
    assert "models" in data10
    assert "RandomForest" in data1["models"]
    assert "RandomForest" in data10["models"]
    assert "probability" in data1["models"]["RandomForest"]
    assert "probability" in data10["models"]["RandomForest"]
    
    prob1 = data1["models"]["RandomForest"]["probability"]
    prob10 = data10["models"]["RandomForest"]["probability"]
    
    assert prob1 != prob10


def test_ab_predict_dead_zone_gone(monkeypatch):
    """POST /api/v1/ab/predict: social_impact 1 vs 10 give different probabilities."""
    c = _make_client()
    
    # Force model A for both requests
    import app.api.ab_test
    monkeypatch.setitem(app.api.ab_test._traffic_split, "model_a", 1.0)
    
    p1 = {**BASE_PROJECT, "social_impact": 1}
    p10 = {**BASE_PROJECT, "social_impact": 10}
    
    r1 = c.post("/api/v1/ab/predict", json=p1)
    r10 = c.post("/api/v1/ab/predict", json=p10)
    
    assert r1.status_code == 200
    assert r10.status_code == 200
    
    data1 = r1.json()
    data10 = r10.json()
    
    assert "probability" in data1
    assert "probability" in data10
    
    assert data1["probability"] != data10["probability"]


# ============================================================================
# (a2, a3) Unit tests for build_features and ml_registry._make_features
# ============================================================================

def test_build_features_scales_social_impact(monkeypatch):
    """app.ml.features.build_features: social_impact 1 → 10.0, 10 → 100.0."""
    from app.ml.features import build_features
    import app.ml.features

    # Monkeypatch the scaler to return input unchanged (pre-existing bug: scaler_v2 expects country_gdp_per_capita)
    class FakeScaler:
        def transform(self, X):
            return X

    monkeypatch.setattr(app.ml.features, "_load_scaler", lambda: FakeScaler())

    p1 = {**BASE_PROJECT, "social_impact": 1}
    p10 = {**BASE_PROJECT, "social_impact": 10}

    df1 = build_features(p1)
    df10 = build_features(p10)

    assert "social_impact" in df1.columns
    assert "social_impact" in df10.columns

    assert df1["social_impact"].iloc[0] == 10.0
    assert df10["social_impact"].iloc[0] == 100.0


def test_ml_registry_make_features_scales_social_impact(monkeypatch):
    """app.ml_registry._make_features: social_impact 1 → 10.0, 10 → 100.0."""
    from app.ml_registry import _make_features
    import app.ml_registry as mlr
    from sklearn.preprocessing import LabelEncoder

    # Set up minimal encodings dict
    encodings = {
        "category": {"Solar Energy": 0.5, "energy": 0.6},
        "region": {"Europe": 0.5, "EU": 0.6}
    }
    monkeypatch.setattr(mlr, "_encodings", encodings)

    p1 = {**BASE_PROJECT, "social_impact": 1, "category": "Solar Energy", "region": "Europe"}
    p10 = {**BASE_PROJECT, "social_impact": 10, "category": "Solar Energy", "region": "Europe"}

    feats1 = _make_features(p1)
    feats10 = _make_features(p10)

    # _make_features returns a dict, not a DataFrame
    assert "social_impact" in feats1
    assert "social_impact" in feats10

    assert feats1["social_impact"] == 10.0
    assert feats10["social_impact"] == 100.0


# ============================================================================
# (b) Refusal: 70 → 422, 10.5 → 422, 10 → 200
# ============================================================================

def test_evaluate_refusals():
    """POST /api/v1/evaluate: 70 → 422, 10.5 → 422, 10 → 200."""
    c = _make_client()
    
    r70 = c.post("/api/v1/evaluate", json={**BASE_PROJECT, "social_impact": 70})
    assert r70.status_code == 422
    
    r10_5 = c.post("/api/v1/evaluate", json={**BASE_PROJECT, "social_impact": 10.5})
    assert r10_5.status_code == 422
    
    r10 = c.post("/api/v1/evaluate", json={**BASE_PROJECT, "social_impact": 10})
    assert r10.status_code == 200


def test_predict_refusals():
    """POST /api/v1/predict: 70 → 422, 10.5 → 422, 10 → 200."""
    c = _make_client()
    
    r70 = c.post("/api/v1/predict", json={**BASE_PROJECT, "social_impact": 70})
    assert r70.status_code == 422
    
    r10_5 = c.post("/api/v1/predict", json={**BASE_PROJECT, "social_impact": 10.5})
    assert r10_5.status_code == 422
    
    r10 = c.post("/api/v1/predict", json={**BASE_PROJECT, "social_impact": 10})
    assert r10.status_code == 200


def test_stacking_refusals():
    """POST /api/v1/predict/stacking: 70 → 422, 10.5 → 422, 10 → 200."""
    c = _make_client()
    
    r70 = c.post("/api/v1/predict/stacking", json={**BASE_PROJECT, "social_impact": 70})
    assert r70.status_code == 422
    
    r10_5 = c.post("/api/v1/predict/stacking", json={**BASE_PROJECT, "social_impact": 10.5})
    assert r10_5.status_code == 422
    
    r10 = c.post("/api/v1/predict/stacking", json={**BASE_PROJECT, "social_impact": 10})
    assert r10.status_code == 200


def test_uncertainty_refusals_and_string_coercion():
    """POST /api/v1/predict/uncertainty: 70 → 422, 10.5 → 422, 10 → 200, "7" → 200, "abc" → 422, social_impact_score:70 → 422."""
    c = _make_client()
    
    r70 = c.post("/api/v1/predict/uncertainty", json={**BASE_PROJECT, "social_impact": 70})
    assert r70.status_code == 422
    
    r10_5 = c.post("/api/v1/predict/uncertainty", json={**BASE_PROJECT, "social_impact": 10.5})
    assert r10_5.status_code == 422
    
    r10 = c.post("/api/v1/predict/uncertainty", json={**BASE_PROJECT, "social_impact": 10})
    assert r10.status_code == 200
    
    # String coercion
    r_str7 = c.post("/api/v1/predict/uncertainty", json={**BASE_PROJECT, "social_impact": "7"})
    assert r_str7.status_code == 200
    
    r_abc = c.post("/api/v1/predict/uncertainty", json={**BASE_PROJECT, "social_impact": "abc"})
    assert r_abc.status_code == 422
    
    # Public alias social_impact_score
    r_alias70 = c.post("/api/v1/predict/uncertainty", json={**BASE_PROJECT, "social_impact_score": 70})
    assert r_alias70.status_code == 422


def test_explain_refusals():
    """POST /api/v1/predict/explain: 70 → 422, 10.5 → 422, 10 → 200."""
    c = _make_client()
    
    r70 = c.post("/api/v1/predict/explain", json={**BASE_PROJECT, "social_impact": 70})
    assert r70.status_code == 422
    
    r10_5 = c.post("/api/v1/predict/explain", json={**BASE_PROJECT, "social_impact": 10.5})
    assert r10_5.status_code == 422
    
    r10 = c.post("/api/v1/predict/explain", json={**BASE_PROJECT, "social_impact": 10})
    assert r10.status_code == 200


def test_explain_waterfall_refusals():
    """POST /api/v1/predict/explain/waterfall: 70 → 422, 10.5 → 422, 10 → 200 (image/png)."""
    c = _make_client()
    
    r70 = c.post("/api/v1/predict/explain/waterfall", json={**BASE_PROJECT, "social_impact": 70})
    assert r70.status_code == 422
    
    r10_5 = c.post("/api/v1/predict/explain/waterfall", json={**BASE_PROJECT, "social_impact": 10.5})
    assert r10_5.status_code == 422
    
    r10 = c.post("/api/v1/predict/explain/waterfall", json={**BASE_PROJECT, "social_impact": 10})
    assert r10.status_code == 200
    assert r10.headers["content-type"].startswith("image/png")


def test_discrepancy_refusals_and_string_coercion():
    """POST /api/v1/calibration/discrepancy: 70 → 422, 10.5 → 422, 10 → 200, "7" → 200, "abc" → 422, social_impact_score:70 → 422."""
    c = _make_client()
    
    r70 = c.post("/api/v1/calibration/discrepancy", json={**BASE_PROJECT, "social_impact": 70})
    assert r70.status_code == 422
    
    r10_5 = c.post("/api/v1/calibration/discrepancy", json={**BASE_PROJECT, "social_impact": 10.5})
    assert r10_5.status_code == 422
    
    r10 = c.post("/api/v1/calibration/discrepancy", json={**BASE_PROJECT, "social_impact": 10})
    assert r10.status_code == 200
    
    # String coercion
    r_str7 = c.post("/api/v1/calibration/discrepancy", json={**BASE_PROJECT, "social_impact": "7"})
    assert r_str7.status_code == 200
    
    r_abc = c.post("/api/v1/calibration/discrepancy", json={**BASE_PROJECT, "social_impact": "abc"})
    assert r_abc.status_code == 422
    
    # Public alias social_impact_score
    r_alias70 = c.post("/api/v1/calibration/discrepancy", json={**BASE_PROJECT, "social_impact_score": 70})
    assert r_alias70.status_code == 422


def test_report_pdf_refusals():
    """POST /api/v1/report/pdf: 70 → 422, 10.5 → 422, 10 → 200 (PDF)."""
    c = _make_client()
    
    r70 = c.post("/api/v1/report/pdf", json={**BASE_PROJECT, "social_impact": 70})
    assert r70.status_code == 422
    
    r10_5 = c.post("/api/v1/report/pdf", json={**BASE_PROJECT, "social_impact": 10.5})
    assert r10_5.status_code == 422
    
    r10 = c.post("/api/v1/report/pdf", json={**BASE_PROJECT, "social_impact": 10})
    assert r10.status_code == 200
    assert r10.headers["content-type"] == "application/pdf"


def test_explain_local_refusals():
    """POST /api/v1/explain/local: 70 → 422, 10.5 → 422, 10 → 200 (numeric keys only)."""
    c = _make_client()
    
    # Numeric keys only (no "country")
    p_base = {
        "budget": BASE_PROJECT["budget"],
        "co2_reduction": BASE_PROJECT["co2_reduction"],
        "duration_months": BASE_PROJECT["duration_months"]
    }
    
    r70 = c.post("/api/v1/explain/local", json={**p_base, "social_impact": 70})
    assert r70.status_code == 422
    
    r10_5 = c.post("/api/v1/explain/local", json={**p_base, "social_impact": 10.5})
    assert r10_5.status_code == 422
    
    r10 = c.post("/api/v1/explain/local", json={**p_base, "social_impact": 10})
    assert r10.status_code == 200


def test_ab_predict_refusals(monkeypatch):
    """POST /api/v1/ab/predict: 70 → 422, 10.5 → 422, 10 → 200."""
    c = _make_client()
    
    import app.api.ab_test
    monkeypatch.setitem(app.api.ab_test._traffic_split, "model_a", 1.0)
    
    r70 = c.post("/api/v1/ab/predict", json={**BASE_PROJECT, "social_impact": 70})
    assert r70.status_code == 422
    
    r10_5 = c.post("/api/v1/ab/predict", json={**BASE_PROJECT, "social_impact": 10.5})
    assert r10_5.status_code == 422
    
    r10 = c.post("/api/v1/ab/predict", json={**BASE_PROJECT, "social_impact": 10})
    assert r10.status_code == 200


# ============================================================================
# (c) beeswarm → 200
# ============================================================================

def test_beeswarm_succeeds():
    """GET /api/v1/explain/beeswarm → 200 (was 500 on main)."""
    c = _make_client()
    r = c.get("/api/v1/explain/beeswarm")
    assert r.status_code == 200


# ============================================================================
# (d) Training rows: ab-comparison n_samples == N, reliability-diagram builder call count == N
# ============================================================================

def test_ab_comparison_samples_all_rows():
    """GET /api/v1/model/ab-comparison: n_samples equals MAX_AB_COMPARISON_ROWS (was 82 on main)."""
    c = _make_client()
    r = c.get("/api/v1/model/ab-comparison")
    assert r.status_code == 200
    
    data = r.json()
    assert "models" in data
    
    # Find n_samples in any model
    n_samples = None
    for model_name, model_data in data["models"].items():
        if "n_samples" in model_data:
            n_samples = model_data["n_samples"]
            break
    
    assert n_samples is not None, "No model reported n_samples"

    import app.api.ab_comparison
    expected = getattr(app.api.ab_comparison, "MAX_AB_COMPARISON_ROWS", None)
    assert n_samples == expected


def test_reliability_diagram_builder_call_count(monkeypatch):
    """GET /api/v1/model/reliability-diagram: builder call count equals MAX_RELIABILITY_ROWS (was ~82 on main)."""
    c = _make_client()
    
    # Count calls to make_features
    call_count = [0]
    import app.main as m
    original_make_features = m.make_features
    
    def counting_wrapper(*args, **kwargs):
        call_count[0] += 1
        return original_make_features(*args, **kwargs)
    
    monkeypatch.setattr(m, "make_features", counting_wrapper)
    
    r = c.get("/api/v1/model/reliability-diagram")
    assert r.status_code == 200

    import app.api.calibration
    expected = getattr(app.api.calibration, "MAX_RELIABILITY_ROWS", None)
    assert call_count[0] == expected


# ============================================================================
# (e) Control: total_score for social_impact 1, 4, 7, 10 equals [36.09, 44.99, 53.9, 62.8] ±0.01
# ============================================================================

def test_evaluate_total_score_control():
    """POST /api/v1/evaluate total_score for social_impact 1,4,7,10 matches expected values (must pass before and after)."""
    c = _make_client()
    
    expected = {
        1: 36.09,
        4: 44.99,
        7: 53.9,
        10: 62.8
    }
    
    for si, exp_score in expected.items():
        r = c.post("/api/v1/evaluate", json={**BASE_PROJECT, "social_impact": si})
        assert r.status_code == 200
        
        data = r.json()
        assert "total_score" in data
        
        actual = data["total_score"]
        assert abs(actual - exp_score) < 0.01, f"social_impact={si}: expected {exp_score}, got {actual}"


# ============================================================================
# (f) Unit test for scales helpers
# ============================================================================

def test_scales_helpers():
    """app.scales: social_impact_to_model(7) == 70, social_impact_from_training(70) == 7."""
    from app.scales import social_impact_to_model, social_impact_from_training
    
    assert social_impact_to_model(7) == 70.0
    assert social_impact_from_training(70) == 7.0


# ============================================================================



def test_raw_dict_routes_missing_social_impact():
    """predict/uncertainty and calibration/discrepancy: missing social_impact → 200 (handlers use defaults)."""
    c = _make_client()
    
    # predict/uncertainty without social_impact (schema default is 5)
    r1 = c.post("/api/v1/predict/uncertainty", json={"budget": 100000, "co2_reduction": 150, "duration_months": 24})
    assert r1.status_code == 200
    
    # calibration/discrepancy without social_impact (.get default is 5)
    r2 = c.post("/api/v1/calibration/discrepancy", json={"budget": 100000, "co2_reduction": 150, "duration_months": 24})
    assert r2.status_code == 200
