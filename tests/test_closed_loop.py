from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def _admin_token():
    r = client.post(
        "/api/v1/auth/login-json",
        json={"username": "admin", "password": "sora2026"},
    )
    return r.json()["access_token"]


def test_auto_retrain_skips_when_no_drift():
    token = _admin_token()

    with patch("app.api.drift.check_drift") as mocked_drift:
        mocked_drift.return_value = {
            "status": "ok",
            "drift_detected": False,
            "window": 50,
            "features": {},
        }

        resp = client.post(
            "/api/v1/mlops/auto-retrain",
            params={"window": 50, "min_samples": 20},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["drift_detected"] is False
    assert data["retrained"] is False
    assert data["reason"] == "drift_not_detected"


def test_auto_retrain_runs_when_drift_detected():
    token = _admin_token()

    with patch("app.api.drift.check_drift") as mocked_drift,          patch("app.api.retrain._do_retrain") as mocked_retrain:
        mocked_drift.return_value = {
            "status": "ok",
            "drift_detected": True,
            "window": 50,
            "features": {"budget": {"drift": True}},
        }
        mocked_retrain.return_value = {
            "status": "success",
            "model_version": "test-v1",
            "metrics": {"roc_auc": 0.88},
        }

        resp = client.post(
            "/api/v1/mlops/auto-retrain",
            params={"window": 50, "min_samples": 20},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["drift_detected"] is True
    assert data["retrained"] is True
