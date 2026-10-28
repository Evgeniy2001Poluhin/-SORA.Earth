import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.auth import require_admin
from app.cache import LRUCache

client = TestClient(app)


@pytest.fixture(autouse=True)
def _authorize_admin():
    # /cache/clear now requires admin (Security C); this suite tests behaviour,
    # not authorization, so it acts as an authorized admin.
    app.dependency_overrides[require_admin] = lambda: {"username": "t", "role": "admin"}
    yield
    app.dependency_overrides.pop(require_admin, None)


def test_cache_stats():
    resp = client.get("/api/v1/cache/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "hits" in data
    assert "misses" in data
    assert "hit_rate" in data


def test_cache_clear():
    resp = client.post("/api/v1/cache/clear")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cache cleared"


def test_evaluate_neither_reads_nor_fills_the_cache(monkeypatch):
    """/evaluate has no result cache any more.

    This test used to require a hit on the second identical call. The cache
    returned its stored response before either database write, so a repeated
    payload was never saved, and a cached saved response would have returned
    another caller's evaluation_id. Two identical calls now leave the counters
    exactly where they were -- and still agree on the score, which is the part
    of the old assertion that was about correctness.
    """
    monkeypatch.setattr("app.external_data.get_country_context", lambda c: {
        "co2_per_capita": 5.0, "renewable_share": 60.0, "life_expectancy": 82.0,
        "gdp_per_capita": 50000, "gini_index": 27.0, "gov_effectiveness": 1.5
    })
    payload = {"name": "Cache Test", "budget": 50000, "co2_reduction": 30, "social_impact": 5, "duration_months": 12, "region": "Norway"}
    before = client.get("/api/v1/cache/stats").json()
    r1 = client.post("/api/v1/evaluate", json=payload)
    r2 = client.post("/api/v1/evaluate", json=payload)
    after = client.get("/api/v1/cache/stats").json()
    assert r1.json()["total_score"] == r2.json()["total_score"]
    assert (after["hits"], after["misses"]) == (before["hits"], before["misses"])


def test_lru_eviction():
    c = LRUCache(maxsize=3, default_ttl=60)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)
    c.set("d", 4)
    assert c.get("a") is None
    assert c.get("d") == 4


def test_lru_ttl():
    import time
    c = LRUCache(maxsize=10, default_ttl=1)
    c.set("x", 42, ttl=1)
    assert c.get("x") == 42
    time.sleep(1.1)
    assert c.get("x") is None
