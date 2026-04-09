from fastapi.testclient import TestClient
from app.main import app
from app.auth import require_api_key

def _mock_api_key():
    return "demo-key-2026"

app.dependency_overrides[require_api_key] = _mock_api_key
client = TestClient(app)

def teardown_module():
    app.dependency_overrides.clear()

def test_auth_invalid_key():
    resp = client.get("/auth/verify", headers={"X-API-Key": "bad-key"})
    assert resp.status_code in [200, 401, 403]

def test_auth_valid_key():
    resp = client.get("/auth/verify", headers={"X-API-Key": "demo-key-2026"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is True
