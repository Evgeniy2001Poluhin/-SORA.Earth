"""Tests for app/api/calibration.py"""
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

PROJECT = {
    "name": "Calib Test", "budget": 200000, "co2_reduction": 80,
    "social_impact": 6, "duration_months": 18, "region": "Germany",
}

DATASET = {"probs": [0.1, 0.4, 0.35, 0.8, 0.9, 0.2], "labels": [0, 0, 1, 1, 1, 0]}


def test_calibration_endpoints():
    """The calibration routes that take a set of predictions and outcomes.

    This requested `/calibration/predict`, `/report`, `/curve`, `/compare`,
    `/metrics` and `/recalibrate` -- without the `/api/v1` prefix, and none of
    them exists -- so all six answered 404, and the test accepted every one.
    """
    for path in ("/api/v1/calibration/brier", "/api/v1/calibration/reliability"):
        r = client.post(path, json=DATASET)
        assert r.status_code == 200, f"{path} returned {r.status_code}: {r.text[:200]}"
        assert 0.0 <= r.json()["brier"] <= 1.0
