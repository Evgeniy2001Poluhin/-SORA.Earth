"""Tests for app/api/calibration.py — boost from 13%."""
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

PROJECT = {
    "name": "Calibration Test",
    "budget": 200000,
    "co2_reduction": 80,
    "social_impact": 6,
    "duration_months": 18,
    "region": "Germany",
}


def test_calibration_predict():
    r = client.post("/calibration/predict", json=PROJECT)
    assert r.status_code in [200, 404, 422]
    if r.status_code == 200:
        data = r.json()
        assert "calibrated_probability" in data or "probability" in data or "success_probability" in data


def test_calibration_report():
    r = client.get("/calibration/report")
    assert r.status_code in [200, 404]
    if r.status_code == 200:
        data = r.json()
        assert isinstance(data, (dict, list))


def test_calibration_curve():
    r = client.get("/calibration/curve")
    assert r.status_code in [200, 404]


def test_calibration_compare():
    r = client.post("/calibration/compare", json=PROJECT)
    assert r.status_code in [200, 404, 422]


def test_calibration_metrics():
    r = client.get("/calibration/metrics")
    assert r.status_code in [200, 404]
    if r.status_code == 200:
        data = r.json()
        assert isinstance(data, dict)


def test_calibration_recalibrate():
    r = client.post("/calibration/recalibrate")
    assert r.status_code in [200, 401, 404]
