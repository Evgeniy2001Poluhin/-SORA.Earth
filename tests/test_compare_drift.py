"""Tests for compare.py and drift.py coverage boost."""
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.api import drift as drift_api

client = TestClient(app)


class TestModelCompare:
    def test_compare_returns_200(self):
        r = client.get("/api/v1/model/compare")
        assert r.status_code == 200
        data = r.json()
        assert "current" in data
        assert "winner" in data

    def test_compare_current_fields(self):
        r = client.get("/api/v1/model/compare")
        data = r.json()
        if data["current"] and isinstance(data["current"], dict):
            for k in ["auc", "f1", "accuracy", "n_estimators", "n_features"]:
                assert k in data["current"]

    def test_compare_winner_logic(self):
        r = client.get("/api/v1/model/compare")
        data = r.json()
        if data["current"] and data["backup"] and isinstance(data["backup"], dict):
            assert data["winner"] in ["current", "backup"]
            for k in ["auc", "f1", "accuracy"]:
                assert k in data["delta"]
        elif data["current"]:
            assert data["winner"] == "current"

    @patch("app.api.compare._load_model", return_value=(None, None))
    def test_compare_no_models(self, mock_load):
        r = client.get("/api/v1/model/compare")
        data = r.json()
        assert data["current"] is None

    @patch("app.api.compare.os.path.exists", return_value=False)
    def test_load_model_missing(self, mock_exists):
        from app.api.compare import _load_model
        rf, sc = _load_model("/fake/rf.pkl", "/fake/sc.pkl")
        assert rf is None and sc is None


class TestDrift:
    def test_drift_returns_200(self):
        r = client.get("/api/v1/model/drift")
        assert r.status_code == 200
        assert "status" in r.json()

    def test_drift_ok_fields(self):
        """Every 200 carries the whole field set now, whatever its status.

        This used to be wrapped in `if data["status"] == "ok"`, which made it
        pass vacuously on the branch CI actually reaches -- there is no
        prediction log there, so the status is "no_log" and the body was never
        inspected. The contract added in #239 guarantees the keys on every
        branch, so the guard is gone and the assertion can fail.
        """
        r = client.get("/api/v1/model/drift")
        data = r.json()
        assert {"status", "drift_detected", "window", "observations", "features", "reason_code"} <= set(data)

    def test_drift_custom_window(self):
        """The window is echoed on every branch, so this no longer needs a guard.

        `features` still does -- it is populated only when something was
        measured, and that guard is semantic rather than vacuous.
        """
        r = client.get("/api/v1/model/drift?window=100")
        assert r.json()["window"] == 100

    def test_drift_features_structure(self):
        r = client.get("/api/v1/model/drift")
        data = r.json()
        if data["status"] == "ok" and "features" in data:
            for col, info in data["features"].items():
                assert "ks_stat" in info
                assert "p_value" in info
                assert "drift" in info

    @patch("app.api.drift.HAS_SCIPY", False)
    def test_drift_no_scipy(self):
        """A missing declared dependency is a fault, not a drift answer (#239).

        This asserted `status == "scipy_not_installed"` on a 200. scipy==1.13.1
        is in both requirements files, so its absence means this instance
        cannot do its job -- and answering 200 let a caller read the body as
        "no drift".
        """
        r = client.get("/api/v1/model/drift")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "unavailable"
        assert body["reason_code"] == "scipy_missing"
        assert body["drift_detected"] is None

    def test_drift_no_log(self):
        """No prediction log is a domain state: 200, and no verdict.

        The patch used to be `os.path.exists -> False` for everything, which
        after #239 means the baseline is missing too -- and that is a 503, not
        a "no log" answer. The blanket patch was describing a condition nobody
        meant. It now says exactly which file is absent.
        """
        def only_the_log_is_missing(path):
            return path != drift_api.PRED_LOG

        with patch("app.api.drift.os.path.exists", side_effect=only_the_log_is_missing):
            r = client.get("/api/v1/model/drift")

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "no_log"
        assert body["drift_detected"] is None, "false would claim drift was looked for"
        assert body["observations"] == 0

    def test_drift_small_window(self):
        r = client.get("/api/v1/model/drift?window=5")
        assert r.status_code == 200
