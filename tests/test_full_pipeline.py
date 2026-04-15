"""Tests for full MLOps pipeline: refresh -> drift -> retrain -> validate -> promote."""
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def _admin_token():
    r = client.post("/api/v1/auth/login-json", json={"username": "admin", "password": "sora2026"})
    return r.json()["access_token"]


def test_full_pipeline_no_drift():
    token = _admin_token()
    with patch("app.external_data.refresh_live_data") as mock_refresh, \
         patch("app.api.drift.check_drift") as mock_drift, \
         patch("app.locks.RedisLock.acquire", return_value=True), \
         patch("app.locks.RedisLock.release"):
        mock_refresh.return_value = {"status": "ok", "countries_fetched": 32}
        mock_drift.return_value = {"status": "ok", "drift_detected": False}
        resp = client.post("/api/v1/mlops/full-pipeline", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["pipeline"] == "full"
    assert data["closed_loop_result"]["retrained"] is False


def test_full_pipeline_drift_and_promote():
    token = _admin_token()
    with patch("app.external_data.refresh_live_data") as mock_refresh, \
         patch("app.api.drift.check_drift") as mock_drift, \
         patch("app.api.retrain._get_current_metrics") as mock_m, \
         patch("app.api.retrain._do_retrain") as mock_retrain, \
         patch("app.locks.RedisLock.acquire", return_value=True), \
         patch("app.locks.RedisLock.release"), \
         patch("app.scheduler._start_retrain_log", return_value=1), \
         patch("app.scheduler._finish_retrain_log"):
        mock_refresh.return_value = {"status": "ok", "countries_fetched": 32}
        mock_drift.return_value = {"status": "ok", "drift_detected": True}
        mock_m.return_value = {"roc_auc": 0.94}
        mock_retrain.return_value = {"status": "success", "metrics": {"roc_auc": 0.96}}
        resp = client.post("/api/v1/mlops/full-pipeline", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    loop = data["closed_loop_result"]
    assert loop["retrained"] is True
    assert loop["promoted"] is True
    assert loop["new_auc"] == 0.96


def test_full_pipeline_requires_admin():
    resp = client.post("/api/v1/mlops/full-pipeline")
    assert resp.status_code in (401, 403)
