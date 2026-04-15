from fastapi.testclient import TestClient
from app.main import app
from app.cache import LRUCache

client = TestClient(app)


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


def test_cache_hit(monkeypatch):
    monkeypatch.setattr("app.external_data.get_country_context", lambda c: {
        "co2_per_capita": 5.0, "renewable_share": 60.0, "life_expectancy": 82.0,
        "gdp_per_capita": 50000, "gini_index": 27.0, "gov_effectiveness": 1.5
    })
    payload = {"name": "Cache Test", "budget": 50000, "co2_reduction": 30, "social_impact": 5, "duration_months": 12, "region": "Norway"}
    r1 = client.post("/api/v1/evaluate", json=payload)
    r2 = client.post("/api/v1/evaluate", json=payload)
    assert r1.json()["total_score"] == r2.json()["total_score"]
    stats = client.get("/api/v1/cache/stats").json()
    assert stats["hits"] >= 1


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
