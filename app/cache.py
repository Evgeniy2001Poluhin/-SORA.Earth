import time
import json
import hashlib
from collections import OrderedDict


class LRUCache:
    def __init__(self, max_size=1000, default_ttl=300):
        self.max_size = max_size
        self.default_ttl = default_ttl
        self.store = OrderedDict()
        self.hits = 0
        self.misses = 0

    def make_key(self, prefix, payload):
        raw = f"{prefix}:{json.dumps(payload, sort_keys=True, default=str)}"
        return hashlib.md5(raw.encode()).hexdigest()

    def get(self, key):
        item = self.store.get(key)
        if not item:
            self.misses += 1
            return None
        value, expires_at = item
        if expires_at is not None and expires_at < time.time():
            del self.store[key]
            self.misses += 1
            return None
        self.store.move_to_end(key)
        self.hits += 1
        return value

    def set(self, key, value, ttl=None):
        if ttl is None:
            ttl = self.default_ttl
        expires_at = time.time() + ttl if ttl > 0 else None
        if key in self.store:
            self.store.move_to_end(key)
        self.store[key] = (value, expires_at)
        while len(self.store) > self.max_size:
            self.store.popitem(last=False)

    def stats(self):
        total = self.hits + self.misses
        return {
            "items": len(self.store),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total * 100, 2) if total > 0 else 0.0,
        }

    def clear(self):
        self.store.clear()
        self.hits = 0
        self.misses = 0


SimpleCache = LRUCache
_cache = LRUCache()
cache = _cache
make_key = _cache.make_key
get = _cache.get
set = _cache.set
stats = _cache.stats
clear = _cache.clear
