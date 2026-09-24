"""Deep tests for calibration module."""
import pytest
from starlette.testclient import TestClient
from app.main import app

client = TestClient(app, raise_server_exceptions=False)
PROJECT = {"budget": 100000, "co2_reduction": 50, "social_impact": 7, "duration_months": 12}

def test_calibration_predict():
    r = client.post("/api/v1/predict/uncertainty", json=PROJECT)
    assert r.status_code == 200, r.text

def test_calibration_keeps_no_history_endpoint():
    """Neither path has ever existed, and the test accepted their 404 as a pass.

    The calibration API is `/calibration/brier`, `/calibration/reliability`,
    `/calibration/discrepancy`, `/model/reliability-diagram` and
    `/predict/uncertainty`; each is exercised elsewhere in this file or in
    tests/test_calibration_boost.py. Nothing stores a calibration history.
    """
    for absent in ("/api/v1/calibration/history", "/api/v1/analytics/calibration/history"):
        assert client.get(absent).status_code == 404, (
            f"{absent} now answers; if calibration gained a history, this test "
            f"should read it rather than deny it"
        )

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
