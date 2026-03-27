from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_metrics_endpoint():
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "http_request" in resp.text or "python_gc" in resp.text


def test_health_endpoint():
    resp = client.get("/health")
    assert resp.status_code == 200


def test_openapi_schema():
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    data = resp.json()
    assert "/auth/login" in data["paths"]
    assert "/analytics/country-ranking" in data["paths"]
    assert "/evaluate" in data["paths"]


def test_docs_available():
    resp = client.get("/docs")
    assert resp.status_code == 200
