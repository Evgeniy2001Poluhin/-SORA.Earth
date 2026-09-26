"""POST /api/v2/predict uses the champion model's own preprocessing artifacts.

Measured defect (on main e6841e4, 2026-09-26):
- POST /api/v2/predict answered 500 for every input.
- It loaded the model `models:/esg-success-predictor@champion` from MLflow
  registry, but built features with `models/scaler_v2.pkl` and
  `models/cat_encodings.json` from disk -- files belonging to the OTHER v2
  model (the one serving /predict/stacking, trained by
  scripts/retrain_ensemble_v2.py).
- The stacking model has 12 features including country_gdp_per_capita;
  `build_features` built 11, so the scaler raised "feature names should match
  ... missing: country_gdp_per_capita".

Fix:
- registry_loader.py: load model + its run's features.json,
  cat_encodings.json, preprocessor/scaler.pkl as one bundle
- features.py: build_features(raw, bundle) uses the bundle's preprocessor
- routes.py: registry_unavailable → 503, preprocessor_unavailable → 503,
  unknown category/region → 422
"""
from unittest.mock import patch, MagicMock
import tempfile
import json
import pickle
import os
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
import pytest


def _make_client():
    """Build TestClient with raise_server_exceptions=False to see 500s as status codes."""
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


def _make_fake_mlflow_artifacts(tmpdir, include_scaler=True):
    """Create fake MLflow artifacts for testing."""
    # Features from train_model_v2.py line 77-79
    features = [
        "budget", "co2_reduction", "social_impact", "duration_months",
        "budget_per_month", "co2_per_dollar", "efficiency_score",
        "impact_ratio", "budget_efficiency",
        "category_enc", "region_enc",
    ]

    # Fake target encodings
    cat_encodings = {
        "category": {"energy": 0.6, "water": 0.5, "waste": 0.55},
        "region": {"EU": 0.7, "NAM": 0.65, "APAC": 0.6}
    }

    # Fit a tiny RF on synthetic data with the correct 11 features
    np.random.seed(42)
    X_fake = np.random.randn(10, 11)
    y_fake = np.random.randint(0, 2, 10)
    model = RandomForestClassifier(n_estimators=10, random_state=42)
    model.fit(X_fake, y_fake)

    # Fit a scaler on the same synthetic data
    scaler = StandardScaler()
    scaler.fit(X_fake)

    # Write artifacts to temp files
    features_path = os.path.join(tmpdir, "features.json")
    with open(features_path, "w") as f:
        json.dump({"features": features}, f)

    cat_path = os.path.join(tmpdir, "cat_encodings.json")
    with open(cat_path, "w") as f:
        json.dump(cat_encodings, f)

    scaler_path = os.path.join(tmpdir, "scaler.pkl")
    if include_scaler:
        with open(scaler_path, "wb") as f:
            pickle.dump(scaler, f)

    return model, features_path, cat_path, scaler_path, scaler


# ============================================================================
# (a) With a fake champion, POST /api/v2/predict → 200 with correct probability
# ============================================================================

def test_v2_predict_with_fake_champion_returns_200(monkeypatch, tmp_path):
    """POST /api/v2/predict → 200 with fake champion, and success_probability matches model output."""
    # Enable MLflow mode
    monkeypatch.setenv("SORA_OFFLINE", "0")
    # Set temporary MLFLOW_TRACKING_URI to avoid file creation in repo
    tmp_mlflow = tmp_path / "mlflow.db"
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{tmp_mlflow}")

    # Create fake artifacts
    model, features_path, cat_path, scaler_path, scaler = _make_fake_mlflow_artifacts(str(tmp_path))

    # Mock MLflow calls
    mock_mv = MagicMock()
    mock_mv.version = "999"
    mock_mv.run_id = "fake-run-id"

    def fake_download_artifacts(artifact_uri, **kwargs):
        if "features.json" in artifact_uri:
            return features_path
        elif "cat_encodings.json" in artifact_uri:
            return cat_path
        elif "scaler.pkl" in artifact_uri:
            return scaler_path
        raise ValueError(f"Unknown artifact: {artifact_uri}")

    with patch("mlflow.sklearn.load_model", return_value=model), \
         patch("mlflow.tracking.MlflowClient.get_model_version_by_alias", return_value=mock_mv), \
         patch("mlflow.artifacts.download_artifacts", side_effect=fake_download_artifacts):

        # Reload to clear cache and load fake artifacts
        import app.ml.registry_loader
        app.ml.registry_loader.reload()

        c = _make_client()

        # Build request
        req = {
            "budget": 100000,
            "co2_reduction": 150,
            "social_impact": 7,
            "duration_months": 24,
            "category": "energy",
            "region": "EU"
        }

        r = c.post("/api/v2/predict", json=req)

        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

        data = r.json()
        assert "success_probability" in data
        assert "success_class" in data
        assert "model_version" in data
        assert data["model_version"] == "999"

        # Verify probability matches what we'd get from the fake model
        # Build features using train_model_v2.py formulas
        budget = 100000
        co2 = 150
        social = 7 * 10.0  # API scale → model scale
        dur = 24
        budget_per_month = budget / dur
        co2_per_dollar = co2 / budget * 1000
        efficiency_score = (co2 * social) / dur
        impact_ratio = social / co2
        budget_efficiency = co2 / budget_per_month

        row = [budget, co2, social, dur, budget_per_month, co2_per_dollar,
               efficiency_score, impact_ratio, budget_efficiency, 0.6, 0.7]  # cat/reg encodings

        # Scale it
        X_test = scaler.transform([row])
        expected_prob = model.predict_proba(X_test)[0][1]

        assert abs(data["success_probability"] - expected_prob) < 0.0001

        # Clean up cache
        app.ml.registry_loader.reload()


# ============================================================================
# (b) The route never reads models/scaler_v2.pkl
# ============================================================================

def test_v2_predict_never_reads_disk_scaler(monkeypatch, tmp_path):
    """POST /api/v2/predict uses bundle's scaler, not models/scaler_v2.pkl."""
    monkeypatch.setenv("SORA_OFFLINE", "0")
    tmp_mlflow = tmp_path / "mlflow.db"
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{tmp_mlflow}")

    model, features_path, cat_path, scaler_path, _ = _make_fake_mlflow_artifacts(str(tmp_path))

    mock_mv = MagicMock()
    mock_mv.version = "999"
    mock_mv.run_id = "fake-run-id"

    def fake_download_artifacts(artifact_uri, **kwargs):
        if "features.json" in artifact_uri:
            return features_path
        elif "cat_encodings.json" in artifact_uri:
            return cat_path
        elif "scaler.pkl" in artifact_uri:
            return scaler_path
        raise ValueError(f"Unknown artifact: {artifact_uri}")

    # Patch open() to raise if scaler_v2.pkl is opened
    original_open = open
    def patched_open(path, *args, **kwargs):
        if "scaler_v2.pkl" in str(path):
            raise RuntimeError("models/scaler_v2.pkl was read")
        return original_open(path, *args, **kwargs)

    with patch("mlflow.sklearn.load_model", return_value=model), \
         patch("mlflow.tracking.MlflowClient.get_model_version_by_alias", return_value=mock_mv), \
         patch("mlflow.artifacts.download_artifacts", side_effect=fake_download_artifacts), \
         patch("builtins.open", patched_open):

        import app.ml.registry_loader
        app.ml.registry_loader.reload()

        c = _make_client()

        req = {
            "budget": 100000,
            "co2_reduction": 150,
            "social_impact": 7,
            "duration_months": 24,
            "category": "energy",
            "region": "EU"
        }

        r = c.post("/api/v2/predict", json=req)

        # Should succeed without opening scaler_v2.pkl
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

        app.ml.registry_loader.reload()


# ============================================================================
# (c) No registered model → 503 with reason_code == "registry_unavailable"
# ============================================================================

def test_v2_predict_no_registered_model_returns_503(monkeypatch, tmp_path):
    """POST /api/v2/predict → 503 with reason_code=registry_unavailable when no model is registered."""
    monkeypatch.setenv("SORA_OFFLINE", "0")
    tmp_mlflow = tmp_path / "mlflow.db"
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{tmp_mlflow}")

    import mlflow.exceptions

    with patch("mlflow.sklearn.load_model", side_effect=mlflow.exceptions.MlflowException("RESOURCE_DOES_NOT_EXIST")):

        import app.ml.registry_loader
        app.ml.registry_loader.reload()

        c = _make_client()

        req = {
            "budget": 100000,
            "co2_reduction": 150,
            "social_impact": 7,
            "duration_months": 24,
            "category": "energy",
            "region": "EU"
        }

        r = c.post("/api/v2/predict", json=req)

        assert r.status_code == 503, f"Expected 503, got {r.status_code}: {r.text}"

        data = r.json()
        assert "reason_code" in data
        assert data["reason_code"] == "registry_unavailable"
        # Verify no URI/path in detail (fixed non-leaking message)
        assert "runs:/" not in data["detail"]
        assert "models:/" not in data["detail"]

        app.ml.registry_loader.reload()


# ============================================================================
# (d) Missing scaler artifact → 503 with reason_code == "preprocessor_unavailable"
# ============================================================================

def test_v2_predict_missing_scaler_artifact_returns_503(monkeypatch, tmp_path):
    """POST /api/v2/predict → 503 with reason_code=preprocessor_unavailable when scaler is missing."""
    monkeypatch.setenv("SORA_OFFLINE", "0")
    tmp_mlflow = tmp_path / "mlflow.db"
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{tmp_mlflow}")

    # Create fake artifacts WITHOUT scaler
    model, features_path, cat_path, scaler_path, _ = _make_fake_mlflow_artifacts(str(tmp_path), include_scaler=False)

    mock_mv = MagicMock()
    mock_mv.version = "999"
    mock_mv.run_id = "fake-run-id"

    def fake_download_artifacts(artifact_uri, **kwargs):
        if "features.json" in artifact_uri:
            return features_path
        elif "cat_encodings.json" in artifact_uri:
            return cat_path
        elif "scaler.pkl" in artifact_uri:
            # Scaler doesn't exist
            raise FileNotFoundError("scaler.pkl not found")
        raise ValueError(f"Unknown artifact: {artifact_uri}")

    with patch("mlflow.sklearn.load_model", return_value=model), \
         patch("mlflow.tracking.MlflowClient.get_model_version_by_alias", return_value=mock_mv), \
         patch("mlflow.artifacts.download_artifacts", side_effect=fake_download_artifacts):

        import app.ml.registry_loader
        app.ml.registry_loader.reload()

        c = _make_client()

        req = {
            "budget": 100000,
            "co2_reduction": 150,
            "social_impact": 7,
            "duration_months": 24,
            "category": "energy",
            "region": "EU"
        }

        r = c.post("/api/v2/predict", json=req)

        assert r.status_code == 503, f"Expected 503, got {r.status_code}: {r.text}"

        data = r.json()
        assert "reason_code" in data
        assert data["reason_code"] == "preprocessor_unavailable"
        # Verify no URI/path in detail (fixed non-leaking message)
        assert "runs:/" not in data["detail"]
        assert ".pkl" not in data["detail"]

        app.ml.registry_loader.reload()


# ============================================================================
# (e) Unknown category → 422 listing the known ones
# ============================================================================

def test_v2_predict_unknown_category_returns_422(monkeypatch, tmp_path):
    """POST /api/v2/predict → 422 with list of known categories when category is unknown."""
    monkeypatch.setenv("SORA_OFFLINE", "0")
    tmp_mlflow = tmp_path / "mlflow.db"
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{tmp_mlflow}")

    model, features_path, cat_path, scaler_path, _ = _make_fake_mlflow_artifacts(str(tmp_path))

    mock_mv = MagicMock()
    mock_mv.version = "999"
    mock_mv.run_id = "fake-run-id"

    def fake_download_artifacts(artifact_uri, **kwargs):
        if "features.json" in artifact_uri:
            return features_path
        elif "cat_encodings.json" in artifact_uri:
            return cat_path
        elif "scaler.pkl" in artifact_uri:
            return scaler_path
        raise ValueError(f"Unknown artifact: {artifact_uri}")

    with patch("mlflow.sklearn.load_model", return_value=model), \
         patch("mlflow.tracking.MlflowClient.get_model_version_by_alias", return_value=mock_mv), \
         patch("mlflow.artifacts.download_artifacts", side_effect=fake_download_artifacts):

        import app.ml.registry_loader
        app.ml.registry_loader.reload()

        c = _make_client()

        req = {
            "budget": 100000,
            "co2_reduction": 150,
            "social_impact": 7,
            "duration_months": 24,
            "category": "UNKNOWN_CATEGORY",
            "region": "EU"
        }

        r = c.post("/api/v2/predict", json=req)

        assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"

        data = r.json()
        assert "detail" in data
        # Check that known categories are listed
        detail = str(data["detail"])
        assert "energy" in detail
        assert "water" in detail
        assert "waste" in detail

        app.ml.registry_loader.reload()


def test_v2_predict_unknown_region_returns_422(monkeypatch, tmp_path):
    """POST /api/v2/predict → 422 with list of known regions when region is unknown."""
    monkeypatch.setenv("SORA_OFFLINE", "0")
    tmp_mlflow = tmp_path / "mlflow.db"
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{tmp_mlflow}")

    model, features_path, cat_path, scaler_path, _ = _make_fake_mlflow_artifacts(str(tmp_path))

    mock_mv = MagicMock()
    mock_mv.version = "999"
    mock_mv.run_id = "fake-run-id"

    def fake_download_artifacts(artifact_uri, **kwargs):
        if "features.json" in artifact_uri:
            return features_path
        elif "cat_encodings.json" in artifact_uri:
            return cat_path
        elif "scaler.pkl" in artifact_uri:
            return scaler_path
        raise ValueError(f"Unknown artifact: {artifact_uri}")

    with patch("mlflow.sklearn.load_model", return_value=model), \
         patch("mlflow.tracking.MlflowClient.get_model_version_by_alias", return_value=mock_mv), \
         patch("mlflow.artifacts.download_artifacts", side_effect=fake_download_artifacts):

        import app.ml.registry_loader
        app.ml.registry_loader.reload()

        c = _make_client()

        req = {
            "budget": 100000,
            "co2_reduction": 150,
            "social_impact": 7,
            "duration_months": 24,
            "category": "energy",
            "region": "UNKNOWN_REGION"
        }

        r = c.post("/api/v2/predict", json=req)

        assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"

        data = r.json()
        assert "detail" in data
        # Check that known regions are listed
        detail = str(data["detail"])
        assert "EU" in detail or "eu" in detail.lower()
        assert "NAM" in detail or "nam" in detail.lower()
        assert "APAC" in detail or "apac" in detail.lower()

        app.ml.registry_loader.reload()


# ============================================================================
# (f) SORA_OFFLINE=1 → 503 with reason_code == "registry_unavailable"
# ============================================================================

def test_v2_predict_offline_mode_returns_503(monkeypatch):
    """POST /api/v2/predict → 503 with reason_code=registry_unavailable when SORA_OFFLINE=1."""
    monkeypatch.setenv("SORA_OFFLINE", "1")

    import app.ml.registry_loader
    app.ml.registry_loader.reload()

    c = _make_client()

    req = {
        "budget": 100000,
        "co2_reduction": 150,
        "social_impact": 7,
        "duration_months": 24,
        "category": "energy",
        "region": "EU"
    }

    r = c.post("/api/v2/predict", json=req)

    assert r.status_code == 503, f"Expected 503, got {r.status_code}: {r.text}"

    data = r.json()
    assert "reason_code" in data
    assert data["reason_code"] == "registry_unavailable"

    app.ml.registry_loader.reload()
