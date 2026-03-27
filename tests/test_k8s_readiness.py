from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_check():
    resp = client.get("/health")
    assert resp.status_code == 200


def test_prometheus_metrics():
    resp = client.get("/metrics")
    assert resp.status_code == 200


def test_mlops_readiness():
    resp = client.get("/mlops/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "model_status" in data


def test_drift_endpoint():
    resp = client.get("/mlops/drift")
    assert resp.status_code == 200


def test_openapi_json():
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    assert "paths" in resp.json()
