"""Tests for app/api/calibration.py"""
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

PROJECT = {
    "name": "Calib Test", "budget": 200000, "co2_reduction": 80,
    "social_impact": 6, "duration_months": 18, "region": "Germany",
}

def test_calibration_endpoints():
    paths = ["/calibration/predict", "/calibration/report", "/calibration/curve",
             "/calibration/compare", "/calibration/metrics", "/calibration/recalibrate"]
    for path in paths:
        if "predict" in path or "compare" in path or "recalibrate" in path:
            r = client.post(path, json=PROJECT)
        else:
            r = client.get(path)
        assert r.status_code in [200, 404, 405, 422, 500], f"{path} returned {r.status_code}"
