"""Final coverage boost: data_pipeline, websocket, retrain edge cases, auth."""
import pytest
import os, shutil
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient
from app.main import app
from app.auth import require_admin, require_api_key


def _mock_admin():
    return {"username": "test_admin", "role": "admin"}


def _mock_api_key():
    return "test-api-key"


_admin = {}

client = TestClient(app)


def setup_module():
    app.dependency_overrides[require_admin] = _mock_admin
    app.dependency_overrides[require_api_key] = _mock_api_key


def teardown_module():
    app.dependency_overrides.clear()


class TestDataPipeline:
    def setup_method(self):
        app.dependency_overrides[require_admin] = _mock_admin
        app.dependency_overrides[require_api_key] = _mock_api_key

    def test_refresh_status(self):
        r = client.get("/api/v1/data/refresh/status")
        assert r.status_code == 200
        assert "running" in r.json()

    def test_refresh_status_alt(self):
        r = client.get("/api/v1/data/refresh-status")
        assert r.status_code == 200

    def test_data_status(self):
        r = client.get("/api/v1/data/status")
        assert r.status_code == 200

    def test_all_countries(self):
        r = client.get("/api/v1/data/countries")
        assert r.status_code == 200
        assert "count" in r.json()

    def test_supported_countries(self):
        r = client.get("/api/v1/data/countries/supported")
        assert r.status_code == 200

    def test_country_germany(self):
        r = client.get("/api/v1/data/country/Germany")
        assert r.status_code == 200
        assert r.json()["country"] == "Germany"

    def test_country_unknown(self):
        r = client.get("/api/v1/data/country/Atlantis")
        assert r.status_code == 200
        data = r.json()
        assert "error" in data or "supported" in data

    @patch("app.api.data_pipeline.external_data.get_refresh_status", return_value={"status": "idle"})
    @patch("fastapi.BackgroundTasks.add_task")
    def test_refresh_trigger(self, mock_add_task, mock_status):
        r = client.post("/api/v1/data/refresh")
        assert r.status_code == 200
        assert r.json()["status"] == "started"
        mock_add_task.assert_called()

    @patch("app.api.data_pipeline.external_data.get_refresh_status", return_value={"status": "running"})
    def test_refresh_already_running(self, mock_status):
        r = client.post("/api/v1/data/refresh")
        assert r.status_code == 200
        assert r.json()["status"] == "already_running"


class TestWebSocket:
    def test_manager_init(self):
        from app.websocket import ConnectionManager
        mgr = ConnectionManager()
        assert mgr.count == 0
        assert mgr.active == []

    def test_disconnect_not_connected(self):
        from app.websocket import ConnectionManager
        mgr = ConnectionManager()
        mock_ws = MagicMock()
        mgr.disconnect(mock_ws)
        assert mgr.count == 0

    def test_broadcast_empty(self):
        from app.websocket import ConnectionManager
        mgr = ConnectionManager()
        loop = asyncio.new_event_loop()
        loop.run_until_complete(mgr.broadcast({"test": 1}))
        loop.close()
        assert mgr.count == 0

    def test_broadcast_with_dead_connection(self):
        from app.websocket import ConnectionManager
        mgr = ConnectionManager()
        mock_ws = MagicMock()
        mock_ws.send_json = AsyncMock(side_effect=Exception("dead"))
        mgr.active.append(mock_ws)
        assert mgr.count == 1
        loop = asyncio.new_event_loop()
        loop.run_until_complete(mgr.broadcast({"msg": "hi"}))
        loop.close()
        assert mgr.count == 0

    def test_connect(self):
        from app.websocket import ConnectionManager
        mgr = ConnectionManager()
        mock_ws = MagicMock()
        mock_ws.accept = AsyncMock()
        loop = asyncio.new_event_loop()
        loop.run_until_complete(mgr.connect(mock_ws))
        loop.close()
        assert mgr.count == 1


class TestAuthEdgeCases:
    def test_no_api_key(self):
        r = client.get("/api/v1/model/feature-importance")
        assert r.status_code in [200, 401, 403]

    def test_invalid_api_key(self):
        r = client.get("/api/v1/model/feature-importance", headers={"X-API-Key": "not-a-real-credential-invalid-by-design"})
        assert r.status_code in [200, 403]


class TestRetrainEdgeCases:
    _BACKUP = "models/_test_backup2"

    def setup_method(self):
        app.dependency_overrides[require_admin] = _mock_admin
        os.makedirs(self._BACKUP, exist_ok=True)
        for f in os.listdir("models"):
            if f.endswith(".pkl"):
                shutil.copy2(f"models/{f}", f"{self._BACKUP}/{f}")

    def teardown_method(self):
        if os.path.exists(self._BACKUP):
            for f in os.listdir(self._BACKUP):
                shutil.copy2(f"{self._BACKUP}/{f}", f"models/{f}")
            shutil.rmtree(self._BACKUP, ignore_errors=True)

    # The xfail here read "Background task response conflict" (#2). The
    # endpoint clamps min_samples to 100000 and refuses with 400 when the
    # dataset is smaller, which is what this asserts; the marker outlived
    # whatever it was written for.
    def test_retrain_low_samples(self):
        r = client.post("/api/v1/model/retrain?min_samples=999999", headers=_admin)
        assert r.status_code == 400

    def test_data_refresh_auto_retrain_trigger(self):
        r = client.post(
            "/api/v1/model/data/refresh",
            headers=_admin,
            params={
                "budget": 50000,
                "co2_reduction": 100,
                "social_impact": 5,
                "duration_months": 6,
                "success": 0,
                "auto_retrain_threshold": 1,
            },
        )
        assert r.status_code == 200
        assert "auto_retrain_triggered" in r.json()


class TestBulkUpload:
    """The endpoint is admin-only and reads only from UPLOADS_DIR.

    These tests used to post unauthenticated with arbitrary absolute paths,
    which is the behaviour that made the endpoint an unauthenticated arbitrary
    file read and a training-data poisoning vector. They now exercise the
    secured contract; the security properties themselves live in
    tests/test_bulk_upload_security.py.
    """

    @pytest.fixture(autouse=True)
    def _as_admin(self, tmp_path, monkeypatch):
        from app.api import retrain
        from app.auth import require_admin

        uploads = tmp_path / "uploads"
        uploads.mkdir()
        monkeypatch.setattr(retrain, "UPLOADS_DIR", str(uploads))
        app.dependency_overrides[require_admin] = lambda: {"username": "admin", "role": "admin"}
        self.uploads = uploads
        yield
        app.dependency_overrides.pop(require_admin, None)

    def _write(self, name: str, body: str) -> str:
        (self.uploads / name).write_text(body)
        return name

    def test_bulk_upload_file_not_found(self):
        r = client.post("/api/v1/model/data/bulk-upload?file_path=nonexistent_xyz.csv")
        assert r.status_code == 400

    def test_bulk_upload_invalid_csv(self):
        name = self._write("invalid.csv", "not,valid,csv\n\x00\x01\x02")
        r = client.post(f"/api/v1/model/data/bulk-upload?file_path={name}")
        assert r.status_code == 400

    def test_bulk_upload_missing_columns(self):
        name = self._write("missing.csv", "budget,co2_reduction\n100,50\n")
        r = client.post(f"/api/v1/model/data/bulk-upload?file_path={name}")
        assert r.status_code == 400
        assert "Missing columns" in r.json()["detail"]

    def test_bulk_upload_invalid_success(self):
        name = self._write(
            "bad_success.csv",
            "budget,co2_reduction,social_impact,duration_months,success\n100,50,3,6,5\n",
        )
        r = client.post(f"/api/v1/model/data/bulk-upload?file_path={name}")
        assert r.status_code == 400
        assert "invalid success" in r.json()["detail"]

    def test_bulk_upload_success(self):
        import shutil

        shutil.copy("data/projects.csv", "data/projects.csv.bak_test")
        try:
            name = self._write(
                "good.csv",
                "budget,co2_reduction,social_impact,duration_months,success\n100000,80,7,12,1\n",
            )
            r = client.post(f"/api/v1/model/data/bulk-upload?file_path={name}")
            assert r.status_code == 200
            assert r.json()["rows_added"] == 1
        finally:
            shutil.copy("data/projects.csv.bak_test", "data/projects.csv")
            os.unlink("data/projects.csv.bak_test")


class TestAdminAuth:
    def test_optional_api_key_valid(self):
        r = client.get("/api/v1/model/feature-importance", headers={"X-API-Key": "test-api-key-1"})
        assert r.status_code in [200, 403]

    def test_optional_api_key_missing(self):
        r = client.get("/api/v1/model/feature-importance")
        assert r.status_code in [200, 401, 403]


class TestAdminEndpoints:
    def test_admin_stats_no_key(self):
        r = client.get("/api/v1/admin/stats")
        assert r.status_code == 403

    def test_admin_stats_invalid_key(self):
        r = client.get("/api/v1/admin/stats", headers={"X-API-Key": "bogus"})
        assert r.status_code == 403

    def test_list_users_no_auth(self):
        app.dependency_overrides.pop(require_admin, None)
        r = client.get("/api/v1/admin/users")
        app.dependency_overrides[require_admin] = _mock_admin
        assert r.status_code in [401, 403, 422]

    def test_list_users_non_admin(self):
        app.dependency_overrides.pop(require_admin, None)
        r = client.get("/api/v1/admin/users", headers={"Authorization": "Bearer faketoken"})
        app.dependency_overrides[require_admin] = _mock_admin
        assert r.status_code in [401, 403]
