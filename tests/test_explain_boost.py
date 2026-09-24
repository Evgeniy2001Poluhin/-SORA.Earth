"""Tests for app/api/explain.py"""
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

PROJECT = {
    "name": "Explain Test", "budget": 250000, "co2_reduction": 90,
    "social_impact": 8, "duration_months": 15, "region": "Sweden",
}

def test_shap_endpoint():
    r = client.post("/api/v1/shap", json=PROJECT)
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), dict)

def test_predict_explain():
    r = client.post("/api/v1/predict/explain", json=PROJECT)
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), dict)

def test_explain_waterfall():
    r = client.post("/api/v1/predict/explain/waterfall", json=PROJECT)
    assert r.status_code == 200, r.text

@pytest.mark.xfail(strict=True, reason=(
    "GET /explain/beeswarm answers 500 'No valid samples' on every call: the 200 "
    "rows it samples from data/projects.csv carry social_impact on a 0-100 scale "
    "and app/validators.py accepts 0-10, so none survives. Which scale is right "
    "is an open decision. strict: this fails the suite once the endpoint works, "
    "so the marker has to be removed with the defect."))
def test_explain_beeswarm():
    r = client.get("/api/v1/explain/beeswarm")
    assert r.status_code == 200, r.text
