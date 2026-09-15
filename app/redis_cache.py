import redis
import json
import os
import logging

logger = logging.getLogger(__name__)
REDIS_URL = os.getenv('REDIS_URL', 'redis://redis:6379/0')

#: Every key that invalidation is allowed to delete lives under this prefix, and
#: nothing else does. Locks (`sora:lock:*`), scheduler status
#: (`sora:scheduler:*`), token revocation (`auth:*`) and drift state (`drift:*`)
#: are deliberately outside it, so a flush cannot reach coordination state. This
#: is why invalidation matches `CACHE_NAMESPACE + '*'` rather than `sora:*`.
CACHE_NAMESPACE = "sora:cache:"

try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    redis_client.ping()
    REDIS_AVAILABLE = True
    logger.info('Redis connected: %s', REDIS_URL)
except Exception as e:
    redis_client = None
    REDIS_AVAILABLE = False
    logger.warning('Redis unavailable: %s', e)

def cache_get(key):
    if not REDIS_AVAILABLE:
        return None
    try:
        data = redis_client.get(key)
        return json.loads(data) if data else None
    except Exception:
        return None

def cache_set(key, value, ttl=300):
    if not REDIS_AVAILABLE:
        return False
    try:
        redis_client.setex(key, ttl, json.dumps(value, default=str))
        return True
    except Exception:
        return False

def cache_delete(key):
    if not REDIS_AVAILABLE:
        return False
    try:
        redis_client.delete(key)
        return True
    except Exception:
        return False

def invalidate_prediction_cache(prefix=None):
    """Delete prediction-cache keys, and only those. Returns how many were removed.

    Walks `sora:cache:*` (or `sora:cache:<prefix>:*` for one model family) with
    SCAN, never KEYS: KEYS is O(N) and blocks Redis for the length of the walk,
    which is not something an HTTP handler should do. Locks and every other
    `sora:*` key are outside this namespace and are never seen here.

    `prefix` is a caller-supplied path segment. It is checked against a strict
    allowlist so it cannot smuggle a `*` or a `:` and widen the pattern past the
    cache namespace; an illegal prefix raises rather than deleting anything.
    """
    if not REDIS_AVAILABLE:
        return 0
    if prefix is not None:
        if not isinstance(prefix, str) or not prefix or not all(
            ch.isalnum() or ch in "._-+" for ch in prefix
        ):
            raise ValueError(f"invalid cache prefix: {prefix!r}")
        pattern = f"{CACHE_NAMESPACE}{prefix}:*"
    else:
        pattern = f"{CACHE_NAMESPACE}*"
    removed = 0
    try:
        for key in redis_client.scan_iter(match=pattern):
            redis_client.delete(key)
            removed += 1
    except Exception as exc:
        logger.warning("cache invalidation failed for %s: %s", pattern, exc)
    return removed


def cache_stats():
    if not REDIS_AVAILABLE:
        return {"available": False}
    try:
        info = redis_client.info("stats")
        mem = redis_client.info("memory")
        cli = redis_client.info("clients")
        return {
            "available": True,
            "connected_clients": cli.get("connected_clients", 0),
            "used_memory": mem.get("used_memory_human", "0"),
            "keyspace_hits": info.get("keyspace_hits", 0),
            "keyspace_misses": info.get("keyspace_misses", 0),
            "total_keys": redis_client.dbsize(),
        }
    except Exception as e:
        return {"available": False, "error": str(e)}
