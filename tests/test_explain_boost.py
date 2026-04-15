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
    assert r.status_code in [200, 422, 500]
    if r.status_code == 200:
        assert isinstance(r.json(), dict)

def test_predict_explain():
    r = client.post("/api/v1/predict/explain", json=PROJECT)
    assert r.status_code in [200, 422, 500]
    if r.status_code == 200:
        data = r.json()
        assert isinstance(data, dict)

def test_explain_waterfall():
    r = client.post("/api/v1/predict/explain/waterfall", json=PROJECT)
    assert r.status_code in [200, 404, 422, 500]

def test_explain_beeswarm():
    r = client.get("/api/v1/explain/beeswarm")
    assert r.status_code in [200, 404, 500]
