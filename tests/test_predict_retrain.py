"""Tests for predict.py (76%) and retrain.py (69%) coverage boost."""
import pytest
import shutil
import os
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

from app.auth import require_admin
from app.main import app

def _mock_admin():
    return {"username": "test_admin", "role": "admin"}

_admin = {}

def setup_module():
    app.dependency_overrides[require_admin] = _mock_admin

def teardown_module():
    app.dependency_overrides.clear()  # no headers needed with override

SAMPLE = {
    "name": "Test Solar",
    "budget": 500000,
    "co2_reduction": 1200,
    "social_impact": 7.5,
    "duration_months": 18,
    "category": "Solar Energy",
    "region": "Europe",
    "lat": 50.0,
    "lon": 10.0,
}

SAMPLE2 = {
    "name": "Test Wind",
    "budget": 300000,
    "co2_reduction": 800,
    "social_impact": 6.0,
    "duration_months": 12,
    "category": "Wind Energy",
    "region": "Asia",
    "lat": 35.0,
    "lon": 105.0,
}


class TestPredict:
    def test_predict_basic(self):
        r = client.post("/api/v1/predict", json=SAMPLE)
        assert r.status_code == 200
        d = r.json()
        assert "prediction" in d
        assert "probability" in d
        assert "probability_v2" in d
        assert "confidence" in d
        assert d["confidence"] in ["high", "medium", "low"]
        assert len(d["confidence_interval"]) == 2

    def test_predict_neural(self):
        r = client.post("/api/v1/predict/neural", json=SAMPLE)
        assert r.status_code == 200
        assert r.json()["model"] == "NeuralNet"

    def test_predict_stacking(self):
        r = client.post("/api/v1/predict/stacking", json=SAMPLE)
        assert r.status_code == 200
        d = r.json()
        assert d["model"] == "StackingEnsemble"
        for m in ["rf", "xgb", "nn"]:
            assert m in d["base_models"]

    def test_predict_compare(self):
        r = client.post("/api/v1/predict/compare", json={"projects": [SAMPLE, SAMPLE2]})
        assert r.status_code == 200
        d = r.json()
        assert len(d["projects"]) == 2
        assert d["projects"][0]["probability"] >= d["projects"][1]["probability"]
        for key in ["RandomForest", "XGBoost", "NeuralNet", "StackingEnsemble"]:
            assert key in d

    def test_predict_explain(self):
        r = client.post("/api/v1/predict/explain", json=SAMPLE)
        assert r.status_code == 200
        d = r.json()
        assert "verdict" in d
        assert "top_features" in d
        assert "all_features" in d
        assert "base_value" in d
        for f in d["all_features"]:
            assert f["direction"] in ["positive", "negative"]
            assert f["impact"] in ["high", "medium", "low"]

    def test_shap_endpoint(self):
        r = client.post("/api/v1/shap", json=SAMPLE)
        assert r.status_code == 200
        d = r.json()
        assert "shap_values" in d
        assert "feature_names" in d

    def test_predictions_history(self):
        r = client.get("/api/v1/predictions/history")
        assert r.status_code == 200

    def test_predictions_export_csv(self):
        client.post("/api/v1/predict", json=SAMPLE)
        r = client.get("/api/v1/predictions/export/csv")
        assert r.status_code in [200, 404]

    def test_predict_validation_error(self):
        bad = {"budget": -1, "social_impact": 99}
        r = client.post("/api/v1/predict", json=bad)
        assert r.status_code == 422


class TestRetrain:
    MODELS_DIR = "models"
    BACKUP_DIR = "models/_test_backup"

    def setup_method(self):
        from app.auth import require_admin
        app.dependency_overrides[require_admin] = lambda: {"username": "test_admin", "role": "admin"}
        # Backup pkl files before retrain tests
        os.makedirs(self.BACKUP_DIR, exist_ok=True)
        for f in os.listdir(self.MODELS_DIR):
            if f.endswith(".pkl"):
                shutil.copy2(os.path.join(self.MODELS_DIR, f), os.path.join(self.BACKUP_DIR, f))

    def teardown_method(self):
        # Restore pkl files after retrain tests
        if os.path.exists(self.BACKUP_DIR):
            for f in os.listdir(self.BACKUP_DIR):
                if f.endswith(".pkl"):
                    shutil.copy2(os.path.join(self.BACKUP_DIR, f), os.path.join(self.MODELS_DIR, f))
            shutil.rmtree(self.BACKUP_DIR, ignore_errors=True)

    def test_model_metrics(self):
        r = client.get("/api/v1/model/metrics")
        assert r.status_code == 200
        assert "metrics" in r.json()

    def test_model_status(self):
        r = client.get("/api/v1/model/status")
        assert r.status_code == 200
        assert "current_threshold" in r.json()

    def test_feature_importance_no_key(self):
        r = client.get("/api/v1/model/feature-importance")
        assert r.status_code in [401, 403]

    def test_prediction_log_stats(self):
        r = client.get("/api/v1/model/prediction-log/stats")
        assert r.status_code == 200
        assert "total" in r.json()

    def test_retrain_endpoint(self):
        from app.auth import require_admin
        app.dependency_overrides[require_admin] = lambda: {"username": "test_admin", "role": "admin"}
        r = client.post("/api/v1/model/retrain?min_samples=5")
        assert r.status_code == 200
        d = r.json()
        assert d["status"] in ("success", "accepted")
        assert d.get("metrics", {}).get("accuracy", 1) > 0

    def test_data_refresh(self):
        from app.auth import require_admin
        app.dependency_overrides[require_admin] = lambda: {"username": "test_admin", "role": "admin"}
        r = client.post("/api/v1/model/data/refresh",
                        params={"budget": 100000, "co2_reduction": 500,
                                "social_impact": 5, "duration_months": 12,
                                "success": 1, "auto_retrain_threshold": 9999})
        assert r.status_code == 200
        assert r.json()["status"] == "added"

    def test_data_refresh_invalid(self):
        from app.auth import require_admin
        app.dependency_overrides[require_admin] = lambda: {"username": "test_admin", "role": "admin"}
        r = client.post("/api/v1/model/data/refresh",
                        params={"budget": 100000, "co2_reduction": 500,
                                "social_impact": 5, "duration_months": 12,
                                "success": 5})
        assert r.status_code == 400

    def test_bulk_upload_missing(self):
        r = client.post("/api/v1/model/data/bulk-upload?file_path=/nonexistent.csv")
        assert r.status_code == 400
