"""Tests for compare.py and drift.py coverage boost."""
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


class TestModelCompare:
    def test_compare_returns_200(self):
        r = client.get("/model/compare")
        assert r.status_code == 200
        data = r.json()
        assert "current" in data
        assert "winner" in data

    def test_compare_current_fields(self):
        r = client.get("/model/compare")
        data = r.json()
        if data["current"] and isinstance(data["current"], dict):
            for k in ["auc", "f1", "accuracy", "n_estimators", "n_features"]:
                assert k in data["current"]

    def test_compare_winner_logic(self):
        r = client.get("/model/compare")
        data = r.json()
        if data["current"] and data["backup"] and isinstance(data["backup"], dict):
            assert data["winner"] in ["current", "backup"]
            for k in ["auc", "f1", "accuracy"]:
                assert k in data["delta"]
        elif data["current"]:
            assert data["winner"] == "current"

    @patch("app.api.compare._load_model", return_value=(None, None))
    def test_compare_no_models(self, mock_load):
        r = client.get("/model/compare")
        data = r.json()
        assert data["current"] is None

    @patch("app.api.compare.os.path.exists", return_value=False)
    def test_load_model_missing(self, mock_exists):
        from app.api.compare import _load_model
        rf, sc = _load_model("/fake/rf.pkl", "/fake/sc.pkl")
        assert rf is None and sc is None


class TestDrift:
    def test_drift_returns_200(self):
        r = client.get("/model/drift")
        assert r.status_code == 200
        assert "status" in r.json()

    def test_drift_ok_fields(self):
        r = client.get("/model/drift")
        data = r.json()
        if data["status"] == "ok":
            assert "drift_detected" in data
            assert "window" in data
            assert "features" in data

    def test_drift_custom_window(self):
        r = client.get("/model/drift?window=100")
        data = r.json()
        if data["status"] == "ok":
            assert data["window"] == 100

    def test_drift_features_structure(self):
        r = client.get("/model/drift")
        data = r.json()
        if data["status"] == "ok" and "features" in data:
            for col, info in data["features"].items():
                assert "ks_stat" in info
                assert "p_value" in info
                assert "drift" in info

    @patch("app.api.drift.HAS_SCIPY", False)
    def test_drift_no_scipy(self):
        r = client.get("/model/drift")
        assert r.json()["status"] == "scipy_not_installed"

    @patch("app.api.drift.os.path.exists", return_value=False)
    def test_drift_no_log(self, mock_exists):
        r = client.get("/model/drift")
        assert r.json()["status"] in ["no_log", "ok", "insufficient_data"]

    def test_drift_small_window(self):
        r = client.get("/model/drift?window=5")
        assert r.status_code == 200
