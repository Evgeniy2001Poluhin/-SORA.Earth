"""Tests for AI Control Layer endpoints."""
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def _admin_token():
    r = client.post('/api/v1/auth/login-json', json={'username': 'admin', 'password': 'sora2026'})
    return r.json()['access_token']


def test_ai_report_ok():
    token = _admin_token()
    r = client.post('/api/v1/admin/ai/report', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    data = r.json()
    assert data['action'] == 'generate_report'
    assert data['status'] == 'success'
    assert data['trigger_source'] == 'ai_agent'
    assert 'retrain' in data['details']
    assert 'predictions' in data['details']


def test_ai_report_requires_admin():
    r = client.post('/api/v1/admin/ai/report')
    assert r.status_code in (401, 403)


def test_ai_retrain_requires_admin():
    r = client.post('/api/v1/admin/ai/retrain')
    assert r.status_code in (401, 403)


def test_ai_refresh_requires_admin():
    r = client.post('/api/v1/admin/ai/refresh')
    assert r.status_code in (401, 403)
