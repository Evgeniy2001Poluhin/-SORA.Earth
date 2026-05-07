"""Tests for AI Teammate agent."""
import pytest
from app.agents.ai_teammate import AITeammate, Observation, THRESHOLDS
from dataclasses import asdict


class TestAITeammateObserve:
    def test_observe_returns_observations(self):
        t = AITeammate(mode="observe")
        obs = t.observe()
        assert isinstance(obs, list)
        assert len(obs) > 0
        for o in obs:
            assert isinstance(o, Observation)
            assert o.category in ("health", "drift", "freshness", "model_quality", "pipeline")
            assert o.severity in ("info", "warning", "critical")

    def test_decide_after_observe(self):
        t = AITeammate(mode="observe")
        t.observe()
        decisions = t.decide()
        assert isinstance(decisions, list)
        assert len(decisions) > 0
        for d in decisions:
            assert d.action in (
                "no_action", "recommend_refresh", "recommend_retrain",
                "execute_refresh", "execute_retrain", "execute_full_pipeline",
                "escalate"
            )

    def test_observe_mode_never_executes(self):
        t = AITeammate(mode="observe")
        report = t.run()
        for d in t.decisions:
            assert d.executed is False
            assert "execute_" not in d.action or d.action.startswith("recommend") or d.action in ("no_action", "escalate", "recommend_refresh", "recommend_retrain")

    def test_run_returns_report(self):
        t = AITeammate(mode="observe")
        report = t.run()
        assert report.timestamp
        assert report.mode == "observe"
        assert isinstance(report.observations, list)
        assert isinstance(report.decisions, list)
        assert isinstance(report.summary, str)


class TestAITeammateAPI:
    @pytest.fixture(autouse=True)
    def setup(self):
        from fastapi.testclient import TestClient
        from app.main import app
        self.client = TestClient(app)

    def _admin_token(self):
        r = self.client.post('/api/v1/auth/login-json',
                             json={"username": "admin", "password": "sora2026"})
        return r.json()["access_token"]

    def test_teammate_status(self):
        token = self._admin_token()
        r = self.client.get('/api/v1/admin/ai-teammate/status',
                            headers={'Authorization': f'Bearer {token}'})
        assert r.status_code == 200
        data = r.json()
        assert 'observations' in data
        assert 'decisions' in data
        assert 'summary' in data
        assert data['mode'] == 'observe'

    def test_teammate_run_observe(self):
        token = self._admin_token()
        r = self.client.post('/api/v1/admin/ai-teammate/run?mode=observe',
                             headers={'Authorization': f'Bearer {token}'})
        assert r.status_code == 200
        data = r.json()
        assert data['mode'] == 'observe'
        for d in data['decisions']:
            assert d['executed'] is False

    def test_teammate_run_auto(self, monkeypatch):
        # Prevent network calls to World Bank when AI teammate triggers refresh
        import app.external_data as ext
        monkeypatch.setattr(ext, "refresh_all_countries",
                            lambda: {"fetched": 32, "total": 32, "countries": []})
        monkeypatch.setattr(ext, "refresh_live_data",
                            lambda trigger_source="test": {"fetched": 32, "total": 32, "countries": []})

        token = self._admin_token()
        r = self.client.post('/api/v1/admin/ai-teammate/run?mode=auto',
                             headers={'Authorization': f'Bearer {token}'})
        assert r.status_code == 200
        data = r.json()
        assert data['mode'] == 'auto'

    def test_teammate_requires_auth(self):
        r = self.client.get('/api/v1/admin/ai-teammate/status')
        assert r.status_code in (401, 403)

    def test_teammate_invalid_mode(self):
        token = self._admin_token()
        r = self.client.post('/api/v1/admin/ai-teammate/run?mode=yolo',
                             headers={'Authorization': f'Bearer {token}'})
        assert r.status_code == 422
