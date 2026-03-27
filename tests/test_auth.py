from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_login_success():
    resp = client.post("/auth/login", json={"username": "admin", "password": "sora2026"})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_login_wrong_password():
    resp = client.post("/auth/login", json={"username": "admin", "password": "wrong"})
    assert resp.status_code == 401


def test_me_with_token():
    login = client.post("/auth/login", json={"username": "analyst", "password": "analyst123"})
    token = login.json()["access_token"]
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["username"] == "analyst"
    assert resp.json()["role"] == "analyst"


def test_me_without_token():
    resp = client.get("/auth/me")
    assert resp.status_code == 403 or resp.status_code == 401


def test_admin_endpoint():
    login = client.post("/auth/login", json={"username": "admin", "password": "sora2026"})
    token = login.json()["access_token"]
    resp = client.get("/admin/users", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert len(resp.json()) == 3


def test_admin_forbidden_for_viewer():
    login = client.post("/auth/login", json={"username": "viewer", "password": "viewer123"})
    token = login.json()["access_token"]
    resp = client.get("/admin/users", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
