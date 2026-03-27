import json
import hashlib
import time
from collections import OrderedDict
from typing import Optional, Any


class LRUCache:
    """In-memory LRU cache with TTL. Swap for Redis in production."""

    def __init__(self, max_size: int = 1000, default_ttl: int = 300):
        self.max_size = max_size
        self.default_ttl = default_ttl
        self._cache: OrderedDict = OrderedDict()
        self._hits = 0
        self._misses = 0

    def _make_key(self, prefix: str, data: dict) -> str:
        raw = json.dumps(data, sort_keys=True, default=str)
        return f"{prefix}:{hashlib.md5(raw.encode()).hexdigest()}"

    def get(self, key: str) -> Optional[Any]:
        if key in self._cache:
            value, expiry = self._cache[key]
            if time.time() < expiry:
                self._cache.move_to_end(key)
                self._hits += 1
                return value
            else:
                del self._cache[key]
        self._misses += 1
        return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        if key in self._cache:
            del self._cache[key]
        elif len(self._cache) >= self.max_size:
            self._cache.popitem(last=False)
        self._cache[key] = (value, time.time() + (ttl or self.default_ttl))

    def invalidate(self, prefix: str):
        keys_to_del = [k for k in self._cache if k.startswith(prefix)]
        for k in keys_to_del:
            del self._cache[k]

    def clear(self):
        self._cache.clear()
        self._hits = 0
        self._misses = 0

    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total * 100, 1) if total > 0 else 0,
            "ttl_default": self.default_ttl,
        }


cache = LRUCache(max_size=1000, default_ttl=300)
