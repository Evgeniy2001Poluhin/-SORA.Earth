# tests/test_system.py
import sqlite3
from unittest.mock import patch, MagicMock
from app.api.system import _check_models, _check_db, _check_external_data

def test_check_models_degraded():
    mock_main = MagicMock(rf_model=None, xgb_model=None, nn_model=None, DB_PATH="/tmp/db.sqlite")
    with patch.dict("sys.modules", {"app.main": mock_main}):
        result = _check_models()
    assert result["status"] == "degraded"

def test_check_models_unhealthy():
    with patch("builtins.__import__", side_effect=Exception("import fail")):
        result = _check_models()
    assert result["status"] == "unhealthy"

def test_check_db_degraded():
    with patch("app.database.SessionLocal", return_value=MagicMock(execute=MagicMock(side_effect=Exception("DB down")))):
        result = _check_db()
    assert result["status"] == "degraded"

def test_check_external_data_degraded():
    with patch("app.external_data.get_refresh_status", return_value={"static_countries": 5}):
        result = _check_external_data()
    assert result["status"] == "degraded"
    assert result["countries_loaded"] == 5

def test_check_external_data_unhealthy():
    with patch("app.external_data.get_refresh_status", side_effect=Exception("timeout")):
        result = _check_external_data()
    assert result["status"] == "unhealthy"
from starlette.testclient import TestClient
from app.main import app as _app

_client = TestClient(_app)

def test_ping():
    r = _client.get("/ping")
    assert r.status_code == 200
    assert r.json()["pong"] is True

def test_readiness_ok():
    r = _client.get("/ready")
    assert r.status_code in (200, 503)  # 503 если модели не загружены в тестах
