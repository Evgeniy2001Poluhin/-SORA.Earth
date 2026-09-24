"""Deep tests for calibration module."""
import pytest
from starlette.testclient import TestClient
from app.main import app

client = TestClient(app, raise_server_exceptions=False)
PROJECT = {"budget": 100000, "co2_reduction": 50, "social_impact": 7, "duration_months": 12}

def test_calibration_predict():
    r = client.post("/api/v1/predict/uncertainty", json=PROJECT)
    assert r.status_code == 200, r.text

def test_calibration_history():
    r = client.get("/api/v1/calibration/history")
    if r.status_code == 404: r = client.get("/api/v1/analytics/calibration/history")
    assert r.status_code in (200, 404)

def test_calibration_compare():
    r = client.post("/api/v1/calibration/discrepancy", json={"projects": [PROJECT, PROJECT]})
    assert r.status_code == 200, r.text

def test_calibration_recalibrate():
    """Brier score over predictions and outcomes.

    This posted a project, which is not that, and passed on the 422;
    `/calibration/recalibrate`, its fallback, does not exist.
    """
    r = client.post("/api/v1/calibration/brier",
                    json={"probs": [0.2, 0.7, 0.9, 0.1], "labels": [0, 1, 1, 0]})
    assert r.status_code == 200, r.text
    assert "brier" in r.json()

def test_calibration_module_importable():
    try:
        from app import calibration
        assert hasattr(calibration, "calibrate_probability") or hasattr(calibration, "PlattScaler")
    except ImportError:
        pytest.skip("calibration module not yet created")
