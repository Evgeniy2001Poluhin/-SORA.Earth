"""Tests for /admin/timeline and /admin/diagnostics endpoints."""
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def _admin_token():
    r = client.post('/api/v1/auth/login-json', json={'username': 'admin', 'password': 'sora2026'})
    return r.json()['access_token']


class TestTimeline:
    def test_timeline_ok(self):
        token = _admin_token()
        r = client.get('/api/v1/admin/timeline?hours=720&limit=10', headers={'Authorization': f'Bearer {token}'})
        assert r.status_code == 200
        data = r.json()
        assert 'events' in data
        assert 'total_events' in data or 'count' in data
        assert 'hours' in data
        assert isinstance(data['events'], list)

    def test_timeline_default_params(self):
        token = _admin_token()
        r = client.get('/api/v1/admin/timeline', headers={'Authorization': f'Bearer {token}'})
        assert r.status_code == 200
        assert r.json()['hours'] == 72

    def test_timeline_requires_admin(self):
        r = client.get('/api/v1/admin/timeline')
        assert r.status_code in (401, 403)

    def test_timeline_events_sorted_desc(self):
        token = _admin_token()
        r = client.get('/api/v1/admin/timeline?hours=720&limit=50', headers={'Authorization': f'Bearer {token}'})
        events = r.json()['events']
        if len(events) >= 2:
            timestamps = [e['timestamp'] for e in events if e['timestamp']]
            assert timestamps == sorted(timestamps, reverse=True)

    def test_timeline_event_structure(self):
        token = _admin_token()
        r = client.get('/api/v1/admin/timeline?hours=720&limit=5', headers={'Authorization': f'Bearer {token}'})
        events = r.json()['events']
        if events:
            e = events[0]
            assert 'type' in e
            assert e['type'] in ('retrain', 'data_refresh')
            assert 'timestamp' in e
            assert 'status' in e


class TestDiagnostics:
    def test_diagnostics_ok(self):
        token = _admin_token()
        r = client.get('/api/v1/admin/diagnostics?hours=720', headers={'Authorization': f'Bearer {token}'})
        assert r.status_code == 200
        data = r.json()
        assert 'period_hours' in data
        assert 'retrain' in data
        assert 'data_refresh' in data
        assert 'predictions' in data
        assert 'top_retrain_errors' in data

    def test_diagnostics_retrain_structure(self):
        token = _admin_token()
        r = client.get('/api/v1/admin/diagnostics?hours=720', headers={'Authorization': f'Bearer {token}'})
        retrain = r.json()['retrain']
        for key in ('total', 'success', 'failed', 'recent', 'last_status', 'last_at', 'last_metrics'):
            assert key in retrain, f'missing key: {key}'

    def test_diagnostics_predictions_structure(self):
        token = _admin_token()
        r = client.get('/api/v1/admin/diagnostics?hours=720', headers={'Authorization': f'Bearer {token}'})
        preds = r.json()['predictions']
        assert 'total' in preds
        assert 'recent' in preds
        assert 'avg_latency_ms' in preds

    def test_diagnostics_requires_admin(self):
        r = client.get('/api/v1/admin/diagnostics')
        assert r.status_code in (401, 403)

    def test_diagnostics_default_hours(self):
        token = _admin_token()
        r = client.get('/api/v1/admin/diagnostics', headers={'Authorization': f'Bearer {token}'})
        assert r.status_code == 200
        assert r.json()['period_hours'] == 24
