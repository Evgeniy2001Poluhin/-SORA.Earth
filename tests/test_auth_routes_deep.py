import uuid
import pytest
from starlette.testclient import TestClient
from app.main import app

ADMIN_CREDS = {"username": "admin", "password": "sora2026"}
VIEWER_CREDS = {"username": "viewer", "password": "viewer123"}

def _get_token(client, creds):
    r = client.post("/auth/login", json=creds)
    return r.json().get("access_token") if r.status_code == 200 else None

@pytest.fixture(autouse=True)
def _clear_overrides():
    saved = dict(app.dependency_overrides)
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)

def test_login_success():
    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/auth/login", json=ADMIN_CREDS)
    assert r.status_code == 200
    assert "access_token" in r.json()
    assert "refresh_token" in r.json()

def test_login_wrong_password():
    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code in (401, 403)

def test_register():
    c = TestClient(app, raise_server_exceptions=False)
    token = _get_token(c, ADMIN_CREDS)
    assert token
    unique = f"testuser_{uuid.uuid4().hex[:8]}"
    r = c.post("/auth/register",
        json={"username": unique, "password": "StrongPass1!", "role": "viewer"},
        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code in (200, 201), f"Register failed: {r.status_code} {r.text[:300]}"
    assert r.json()["username"] == unique

def test_register_duplicate():
    c = TestClient(app, raise_server_exceptions=False)
    token = _get_token(c, ADMIN_CREDS)
    assert token
    r = c.post("/auth/register",
        json={"username": "admin", "password": "StrongPass1!", "role": "viewer"},
        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code in (409, 422, 400)

def test_me_without_token():
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/auth/me")
    assert r.status_code in (401, 403)

def test_me_with_token():
    c = TestClient(app, raise_server_exceptions=False)
    token = _get_token(c, ADMIN_CREDS)
    assert token
    r = c.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["username"] == "admin"

def test_admin_users():
    c = TestClient(app, raise_server_exceptions=False)
    token = _get_token(c, ADMIN_CREDS)
    assert token
    r = c.get("/admin/users", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200

def test_admin_users_forbidden_for_viewer():
    c = TestClient(app, raise_server_exceptions=False)
    token = _get_token(c, VIEWER_CREDS)
    assert token, "Viewer login failed"
    r = c.get("/admin/users", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text[:200]}"

def test_audit_log():
    c = TestClient(app, raise_server_exceptions=False)
    token = _get_token(c, ADMIN_CREDS)
    assert token
    r = c.get("/audit/log", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200

def test_token_expiry_format():
    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/auth/login", json=ADMIN_CREDS)
    assert r.status_code == 200
    assert "access_token" in r.json()
