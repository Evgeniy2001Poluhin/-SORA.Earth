from fastapi.testclient import TestClient
from app.main import app
from app.cache import LRUCache

client = TestClient(app)


def test_cache_stats():
    resp = client.get("/cache/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "hits" in data
    assert "misses" in data
    assert "hit_rate" in data


def test_cache_clear():
    resp = client.post("/cache/clear")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cache cleared"


def test_cache_hit():
    payload = {"name": "Cache Test", "budget": 50000, "co2_reduction": 30, "social_impact": 5, "duration_months": 12, "region": "Norway"}
    r1 = client.post("/evaluate", json=payload)
    r2 = client.post("/evaluate", json=payload)
    assert r1.json()["total_score"] == r2.json()["total_score"]
    stats = client.get("/cache/stats").json()
    assert stats["hits"] >= 1


def test_lru_eviction():
    c = LRUCache(max_size=3, default_ttl=60)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)
    c.set("d", 4)
    assert c.get("a") is None
    assert c.get("d") == 4


def test_lru_ttl():
    import time
    c = LRUCache(max_size=10, default_ttl=1)
    c.set("x", 42, ttl=1)
    assert c.get("x") == 42
    time.sleep(1.1)
    assert c.get("x") is None
