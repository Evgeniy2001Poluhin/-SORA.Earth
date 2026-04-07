"""Final coverage boost: data_pipeline, websocket, retrain edge cases, auth."""
import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


class TestDataPipeline:
    def test_refresh_status(self):
        r = client.get("/data/refresh/status")
        assert r.status_code == 200
        assert "running" in r.json()

    def test_refresh_status_alt(self):
        r = client.get("/data/refresh-status")
        assert r.status_code == 200

    def test_data_status(self):
        r = client.get("/data/status")
        assert r.status_code == 200

    def test_all_countries(self):
        r = client.get("/data/countries")
        assert r.status_code == 200
        assert "count" in r.json()

    def test_supported_countries(self):
        r = client.get("/data/countries/supported")
        assert r.status_code == 200

    def test_country_germany(self):
        r = client.get("/data/country/Germany")
        assert r.status_code == 200
        data = r.json()
        assert data["country"] == "Germany"

    def test_country_unknown(self):
        r = client.get("/data/country/Atlantis")
        assert r.status_code == 200
        data = r.json()
        assert "error" in data or "supported" in data

    @patch("app.api.data_pipeline.external_data.refresh_live_data", return_value={"status": "ok"})
    def test_refresh_trigger(self, mock_refresh):
        r = client.post("/data/refresh")
        assert r.status_code == 200
        assert r.json()["status"] in ["started", "already_running"]
        mock_refresh.assert_called_once()

    @patch("app.api.data_pipeline._refresh_job", {"running": True, "result": None})
    def test_refresh_already_running(self):
        r = client.post("/data/refresh")
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
        r = client.get("/model/feature-importance")
        assert r.status_code in [401, 403]

    def test_invalid_api_key(self):
        r = client.get("/model/feature-importance",
                       headers={"X-API-Key": "invalid-key-12345"})
        assert r.status_code == 403


class TestRetrainEdgeCases:
    def test_retrain_low_samples(self):
        r = client.post("/model/retrain?min_samples=999999")
        assert r.status_code == 400

    def test_data_refresh_auto_retrain_trigger(self):
        r = client.post("/model/data/refresh",
                        params={"budget": 50000, "co2_reduction": 100,
                                "social_impact": 5, "duration_months": 6,
                                "success": 0, "auto_retrain_threshold": 1})
        assert r.status_code == 200
        d = r.json()
        assert "auto_retrain_triggered" in d
