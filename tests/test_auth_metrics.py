from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_metrics_endpoint():
    resp = client.get("/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert "uptime_seconds" in data
    assert "counters" in data


def test_prometheus_format():
    resp = client.get("/metrics/prometheus")
    assert resp.status_code == 200
    assert "uptime_seconds" in resp.text


def test_rate_limit_status():
    resp = client.get("/rate-limit/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "used" in data
    assert "limit" in data


def test_auth_no_key():
    resp = client.get("/auth/verify")
    assert resp.status_code == 403


def test_auth_invalid_key():
    resp = client.get("/auth/verify", headers={"X-API-Key": "bad-key"})
    assert resp.status_code == 403


def test_auth_valid_key():
    resp = client.get("/auth/verify", headers={"X-API-Key": "demo-key-2026"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "user"


def test_admin_with_user_key():
    resp = client.get("/admin/stats", headers={"X-API-Key": "demo-key-2026"})
    assert resp.status_code == 403


def test_admin_with_admin_key():
    resp = client.get("/admin/stats", headers={"X-API-Key": "admin-key-2026"})
    assert resp.status_code == 200
    assert "metrics" in resp.json()


def test_rate_limiter_enforces():
    from app.rate_limit import RateLimiter
    rl = RateLimiter(max_requests=3, window_seconds=60)
    rl.check("test-ip")
    rl.check("test-ip")
    rl.check("test-ip")
    try:
        rl.check("test-ip")
        assert False, "Should have raised"
    except Exception as e:
        assert "429" in str(e.status_code)


def test_metrics_count_increases():
    m1 = client.get("/metrics").json()
    count1 = m1["counters"].get("http_requests_total", 0)
    client.get("/health")
    m2 = client.get("/metrics").json()
    count2 = m2["counters"].get("http_requests_total", 0)
    assert count2 > count1
