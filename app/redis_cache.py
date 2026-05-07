import redis
import json
import os
import logging

logger = logging.getLogger(__name__)
REDIS_URL = os.getenv('REDIS_URL', 'redis://redis:6379/0')

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
