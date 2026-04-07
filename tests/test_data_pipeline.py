"""Tests for data pipeline and model retrain endpoints."""
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

from app.auth import require_admin
from app.main import app

def _mock_admin():
    return {"username": "test_admin", "role": "admin"}

app.dependency_overrides[require_admin] = _mock_admin
_admin = {}

def setup_module():
    app.dependency_overrides[require_admin] = _mock_admin

def teardown_module():
    app.dependency_overrides.clear()  # no headers needed with override


class TestDataPipeline:
    def test_data_status(self):
        r = client.get("/data/status")
        assert r.status_code == 200
        data = r.json()
        assert "static_countries" in data
        assert data["static_countries"] >= 30

    def test_countries_list(self):
        r = client.get("/data/countries")
        assert r.status_code == 200
        data = r.json()
        assert "count" in data
        assert data["count"] >= 30
        assert "Germany" in data["countries"]

    def test_country_germany(self):
        r = client.get("/data/country/Germany")
        assert r.status_code == 200
        data = r.json()
        assert data["country"] == "Germany"

    def test_country_not_found(self):
        r = client.get("/data/country/Atlantis")
        assert r.status_code == 200
        data = r.json()
        assert "error" in data

    def test_supported_countries(self):
        r = client.get("/data/countries/supported")
        assert r.status_code == 200
        data = r.json()
        assert "Germany" in data
        assert len(data) >= 30

    @pytest.mark.integration
    def test_refresh_starts(self):
        r = client.post("/data/refresh")
        assert r.status_code == 200
        assert r.json()["status"] in ("started", "already_running")

    def test_refresh_status(self):
        r = client.get("/data/refresh-status")
        assert r.status_code == 200
        assert "running" in r.json()


class TestModelRetrain:
    def test_model_metrics(self):
        r = client.get("/model/metrics")
        assert r.status_code == 200
        data = r.json()
        assert "metrics" in data
        assert "models_available" in data

    def test_model_status(self):
        r = client.get("/model/status")
        assert r.status_code == 200
        data = r.json()
        assert "current_threshold" in data

    def test_feature_importance(self):
        r = client.get("/model/feature-importance", headers={"X-API-Key": "demo-key-2026"})
        assert r.status_code == 200
        data = r.json()
        assert "features" in data
        assert len(data["features"]) == 9
        assert data["features"][0]["importance"] >= data["features"][-1]["importance"]

    def test_prediction_log_stats(self):
        r = client.get("/model/prediction-log/stats")
        assert r.status_code == 200
        assert "total" in r.json()

    def test_retrain(self):
        from app.auth import require_admin
        app.dependency_overrides[require_admin] = _mock_admin
        r = client.post("/model/retrain?min_samples=50")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] in ("success", "accepted")
        assert data.get("metrics", {}).get("accuracy", 1) > 0
        assert data.get("models_reloaded", True) is True
