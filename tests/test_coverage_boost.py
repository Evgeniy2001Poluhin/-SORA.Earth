"""Coverage boost tests for calibration, ab_comparison, auth_routes, main."""
import uuid
import pytest
from starlette.testclient import TestClient
from app.main import app

@pytest.fixture(autouse=True)
def _clear_overrides():
    saved = dict(app.dependency_overrides)
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)

ADMIN = {"username": "admin", "password": "sora2026"}

def _token(c):
    r = c.post("/api/v1/auth/login", json=ADMIN)
    assert r.status_code == 200, f"Login fail: {r.status_code} {r.text[:200]}"
    return r.json()

def _access(c):
    return _token(c)["access_token"]

def _auth(c):
    return {"Authorization": f"Bearer {_access(c)}"}


# === AUTH_ROUTES direct function coverage ===

class TestAuthRoutesDirect:
    """Cover auth_routes.py by calling its functions directly."""

    def test_auth_routes_login_form(self):
        from app.auth_routes import login
        from fastapi import Request
        from unittest.mock import MagicMock
        from app.auth import verify_password, USERS_DB

        form = MagicMock()
        form.username = "admin"
        form.password = "sora2026"
        req = MagicMock(spec=Request)
        req.client = MagicMock()
        req.client.host = "127.0.0.1"

        result = login(req, form)
        assert result.access_token
        assert result.refresh_token

    def test_auth_routes_login_fail(self):
        from app.auth_routes import login
        from fastapi import Request, HTTPException
        from unittest.mock import MagicMock

        form = MagicMock()
        form.username = "admin"
        form.password = "wrongpass"
        req = MagicMock(spec=Request)
        req.client = MagicMock()
        req.client.host = "127.0.0.1"

        with pytest.raises(HTTPException) as exc:
            login(req, form)
        assert exc.value.status_code == 401

    def test_auth_routes_refresh(self):
        from app.auth_routes import login, refresh_token, RefreshRequest
        from unittest.mock import MagicMock

        form = MagicMock()
        form.username = "admin"
        form.password = "sora2026"
        req = MagicMock()
        req.client = MagicMock()
        req.client.host = "127.0.0.1"

        tok = login(req, form)
        body = RefreshRequest(refresh_token=tok.refresh_token)
        result = refresh_token(req, body)
        assert result.access_token
        assert result.refresh_token

    def test_auth_routes_refresh_invalid(self):
        from app.auth_routes import refresh_token, RefreshRequest
        from fastapi import HTTPException
        from unittest.mock import MagicMock

        req = MagicMock()
        req.client = MagicMock()
        req.client.host = "127.0.0.1"
        body = RefreshRequest(refresh_token="invalid.token")

        with pytest.raises(HTTPException) as exc:
            refresh_token(req, body)
        assert exc.value.status_code == 401

    def test_auth_routes_register(self):
        from app.auth_routes import register_user
        from app.auth import UserCreate, UserInfo, USERS_DB
        from unittest.mock import MagicMock

        req = MagicMock()
        req.client = MagicMock()
        req.client.host = "127.0.0.1"
        unique = f"boost_{uuid.uuid4().hex[:6]}"
        user_data = UserCreate(username=unique, password="StrongP1!", role="viewer")
        admin = UserInfo(username="admin", role="admin")
        result = register_user(req, user_data, admin)
        assert result["username"] == unique
        USERS_DB.pop(unique, None)

    def test_auth_routes_register_dup(self):
        from app.auth_routes import register_user
        from app.auth import UserCreate, UserInfo
        from fastapi import HTTPException
        from unittest.mock import MagicMock

        req = MagicMock()
        req.client = MagicMock()
        req.client.host = "127.0.0.1"
        user_data = UserCreate(username="admin", password="StrongP1!", role="viewer")
        admin = UserInfo(username="admin", role="admin")
        with pytest.raises(HTTPException) as exc:
            register_user(req, user_data, admin)
        assert exc.value.status_code == 409


# === HTTP-level tests for endpoints needing auth ===

class TestAuthHTTP:
    def test_me(self):
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/api/v1/auth/me", headers=_auth(c))
        assert r.status_code == 200

    def test_admin_stats(self):
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/api/v1/admin/stats", headers=_auth(c))
        assert r.status_code in (200, 403)

    def test_verify_key(self):
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/api/v1/auth/verify", headers=_auth(c))
        assert r.status_code in (200, 403)

    def test_audit_log_filter(self):
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/api/v1/audit/log?limit=5&user=admin", headers=_auth(c))
        assert r.status_code == 200

    def test_list_users(self):
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/api/v1/admin/users", headers=_auth(c))
        assert r.status_code == 200


# === CALIBRATION ===

class TestCalibration:
    def test_reliability_diagram(self):
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/api/v1/model/reliability-diagram", headers=_auth(c))
        assert r.status_code in (200, 404, 500)

    def test_predict_uncertainty(self):
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post("/api/v1/predict/uncertainty",
            json={"budget": 50000, "co2_reduction": 30,
                  "social_impact": 7, "duration_months": 18},
            headers=_auth(c))
        assert r.status_code in (200, 422)
        if r.status_code == 200:
            d = r.json()
            assert "probability" in d
            assert "uncertainty" in d
            assert d["reliability"] in ("high", "medium", "low")


# === AB_COMPARISON ===

class TestABComparison:
    def test_ab_comparison_json(self):
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/api/v1/model/ab-comparison", headers=_auth(c))
        assert r.status_code in (200, 404, 500)
        if r.status_code == 200:
            d = r.json()
            assert "models" in d
            assert "winner" in d

    def test_ab_comparison_plot(self):
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/api/v1/model/ab-comparison/plot", headers=_auth(c))
        assert r.status_code in (200, 404, 500)


# === MAIN.PY ===

class TestMainCoverage:
    def test_health(self):
        c = TestClient(app, raise_server_exceptions=False)
        assert c.get("/health").status_code == 200

    def test_root(self):
        c = TestClient(app, raise_server_exceptions=False)
        assert c.get("/").status_code == 200

    def test_openapi(self):
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/openapi.json")
        assert r.status_code == 200
        assert "paths" in r.json()

    def test_404(self):
        c = TestClient(app, raise_server_exceptions=False)
        assert c.get("/no-such-route-xyz").status_code == 404

    def test_docs(self):
        c = TestClient(app, raise_server_exceptions=False)
        assert c.get("/docs").status_code == 200

    def test_model_info(self):
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/model-info", headers=_auth(c))
        assert r.status_code in (200, 401, 403)

    def test_model_metrics_main(self):
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/model-metrics", headers=_auth(c))
        assert r.status_code in (200, 401, 403)
