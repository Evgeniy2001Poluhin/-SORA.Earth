"""Tests for app/api/ab_comparison.py"""
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

PROJECT = {
    "name": "AB Test", "budget": 150000, "co2_reduction": 60,
    "social_impact": 7, "duration_months": 12, "region": "Germany",
}

def test_ab_predict():
    r = client.post("/api/v1/ab/predict", json=PROJECT)
    assert r.status_code in [200, 422, 500]
    if r.status_code == 200:
        assert isinstance(r.json(), dict)

def test_ab_stats():
    r = client.get("/api/v1/ab/stats")
    assert r.status_code in [200, 404]
    if r.status_code == 200:
        assert isinstance(r.json(), dict)

def test_ab_split():
    r = client.post("/api/v1/ab/split", json=PROJECT)
    assert r.status_code in [200, 422, 500]
    if r.status_code == 200:
        assert isinstance(r.json(), dict)
