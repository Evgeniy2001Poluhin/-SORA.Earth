import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient
from app.main import app, make_features_v2
from app.validators import ProjectInput

client = TestClient(app)

SAMPLE = {"budget": 50000, "co2_reduction": 40, "social_impact": 5, "duration_months": 12}

# --- make_features_v2 unit tests ---

def test_make_features_v2_shape():
    p = ProjectInput(**SAMPLE)
    df = make_features_v2(p, "Solar Energy", "Europe")
    assert df.shape == (1, 11), f"Expected (1,11), got {df.shape}"

def test_make_features_v2_no_nan():
    p = ProjectInput(**SAMPLE)
    df = make_features_v2(p, "Wind Energy", "Asia")
    assert not df.isnull().values.any(), "NaN values in features"

def test_make_features_v2_unknown_category_fallback():
    p = ProjectInput(**SAMPLE)
    df = make_features_v2(p, "UnknownCategory", "UnknownRegion")
    assert df.shape == (1, 11)

@pytest.mark.parametrize("category,region", [
    ("Solar Energy", "Europe"),
    ("Wind Energy", "Asia"),
    ("Hydro", "Africa"),
])
def test_make_features_v2_parametrized(category, region):
    p = ProjectInput(**SAMPLE)
    df = make_features_v2(p, category, region)
    assert df.shape == (1, 11)

# --- /predict endpoint tests ---

def test_predict_returns_both_probabilities():
    r = client.post("/predict", json=SAMPLE)
    assert r.status_code == 200
    d = r.json()
    assert "probability" in d, "Missing v1 probability"
    assert "probability_v2" in d, "Missing v2 probability"

def test_predict_probability_range():
    r = client.post("/predict", json=SAMPLE)
    d = r.json()
    assert 0 <= d["probability"] <= 100
    assert 0 <= d["probability_v2"] <= 100

def test_predict_v2_not_none():
    r = client.post("/predict", json=SAMPLE)
    assert r.json()["probability_v2"] is not None

def test_predict_model_field():
    r = client.post("/predict", json=SAMPLE)
    assert r.json()["model"] == "RandomForest"

def test_predict_ab_divergence():
    """Flag if v1 and v2 diverge by more than 50 points — sanity check."""
    r = client.post("/predict", json=SAMPLE)
    d = r.json()
    diff = abs(d["probability"] - d["probability_v2"])
    assert diff < 50, f"v1/v2 divergence too high: {diff:.1f}%"
