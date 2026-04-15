import time
import uuid
import logging

logger = logging.getLogger(__name__)


class RedisLock:
    def __init__(self, key: str, timeout: int = 300):
        self.key = key
        self.timeout = timeout
        self.token = str(uuid.uuid4())
        self.acquired = False

    def acquire(self) -> bool:
        try:
            from app.redis_cache import redis_client, REDIS_AVAILABLE
            if not REDIS_AVAILABLE:
                logger.warning("Redis unavailable, lock disabled for key=%s", self.key)
                return True
            self.acquired = bool(redis_client.set(self.key, self.token, nx=True, ex=self.timeout))
            return self.acquired
        except Exception as e:
            logger.warning("Lock acquire failed for %s: %s", self.key, e)
            return True

    def release(self):
        try:
            from app.redis_cache import redis_client, REDIS_AVAILABLE
            if not REDIS_AVAILABLE:
                return
            current = redis_client.get(self.key)
            if current:
                current = current.decode() if isinstance(current, bytes) else current
            if current == self.token:
                redis_client.delete(self.key)
        except Exception as e:
            logger.warning("Lock release failed for %s: %s", self.key, e)

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError(f"Lock already held: {self.key}")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()


def acquire_lock_or_fail(key: str, timeout: int = 300):
    lock = RedisLock(key=key, timeout=timeout)
    if not lock.acquire():
        raise RuntimeError(f"Another process is already running: {key}")
    return lock
