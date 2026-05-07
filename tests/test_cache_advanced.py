"""Advanced cache tests."""
import time
from app.cache import LRUCache


def test_cache_overwrite():
    c = LRUCache(maxsize=10, default_ttl=60)
    c.set("k", "v1")
    c.set("k", "v2")
    assert c.get("k") == "v2"


def test_cache_stats_after_ops():
    c = LRUCache(maxsize=10, default_ttl=60)
    c.set("a", 1)
    c.get("a")
    c.get("b")
    s = c.stats()
    assert s["hits"] == 1
    assert s["misses"] == 1
    assert s["hit_rate"] == 50.0


def test_cache_clear_resets():
    c = LRUCache(maxsize=10, default_ttl=60)
    c.set("a", 1)
    c.clear()
    assert c.get("a") is None
    s = c.stats()
    assert s["items"] == 0
    assert s["hits"] == 0


def test_cache_make_key_deterministic():
    c = LRUCache()
    k1 = c.make_key("test", {"a": 1, "b": 2})
    k2 = c.make_key("test", {"b": 2, "a": 1})
    assert k1 == k2


def test_cache_different_prefix():
    c = LRUCache()
    k1 = c.make_key("eval", {"a": 1})
    k2 = c.make_key("pred", {"a": 1})
    assert k1 != k2
