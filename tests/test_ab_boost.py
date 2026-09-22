"""Tests for app/api/ab_comparison.py"""
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.auth import require_admin

client = TestClient(app)


@pytest.fixture(autouse=True)
def _authorize_admin():
    # /ab/split now requires admin (Security C); this suite tests behaviour.
    app.dependency_overrides[require_admin] = lambda: {"username": "t", "role": "admin"}
    yield
    app.dependency_overrides.pop(require_admin, None)

PROJECT = {
    "name": "AB Test", "budget": 150000, "co2_reduction": 60,
    "social_impact": 7, "duration_months": 12, "region": "Germany",
}

def test_ab_predict():
    r = client.post("/api/v1/ab/predict", json=PROJECT)
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), dict)

def test_ab_stats():
    r = client.get("/api/v1/ab/stats")
    assert r.status_code in [200, 404]
    if r.status_code == 200:
        assert isinstance(r.json(), dict)

def test_ab_split(monkeypatch):
    """Sets the split and sees it take.

    This posted PROJECT, which is not a split body, and passed on the 422.
    The split is module state, so it is put back when the test ends.
    """
    import app.api.ab_test as ab

    monkeypatch.setitem(ab._traffic_split, "model_a", ab._traffic_split["model_a"])
    r = client.post("/api/v1/ab/split", json={"model_a_pct": 0.3})
    assert r.status_code == 200, r.text
    assert r.json()["traffic_split"]["model_a"] == 0.3
