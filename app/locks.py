import time
import uuid
import logging

logger = logging.getLogger(__name__)


ACQUIRED = "acquired"
HELD = "held"
UNAVAILABLE = "unavailable"


class StrictLock:
    """A lock that says which of three things happened.

    `RedisLock.acquire()` returns True when the lock was taken, and *also*
    when Redis is unreachable or the call raised -- fail-open, deliberately,
    so that an unavailable Redis does not stop a job from running. That is a
    reasonable default for jobs where a duplicate run is harmless.

    It is not reasonable for a mass write path. A caller cannot tell "I hold
    the lock" from "there is no lock", so the assurance "a manual run and a
    scheduled run cannot overlap" is not true when Redis is down -- which is
    when both are most likely to be tried.

    This reports `acquired`, `held` or `unavailable` and lets the caller
    decide. It does not replace RedisLock; other jobs keep the fail-open
    behaviour that suits them.

    A Redis lock is not the last line of defence either: it says who may
    *start*. Two processes deciding the same period is new is a
    check-then-act inside the database, and only the database can serialise
    that -- see the advisory lock in refresh_indicator_history.
    """

    def __init__(self, key: str, timeout: int = 300):
        self.key = key
        self.timeout = timeout
        self.token = str(uuid.uuid4())
        self.state = None

    def acquire(self) -> str:
        try:
            from app.redis_cache import redis_client, REDIS_AVAILABLE

            if not REDIS_AVAILABLE:
                self.state = UNAVAILABLE
                return UNAVAILABLE
            got = bool(redis_client.set(
                self.key, self.token, nx=True, ex=self.timeout))
            self.state = ACQUIRED if got else HELD
            return self.state
        except Exception as e:
            # An exception is not evidence that nobody holds the lock, so it
            # cannot be reported as free.
            logger.warning("strict lock unavailable for %s: %s", self.key, e)
            self.state = UNAVAILABLE
            return UNAVAILABLE

    def release(self) -> None:
        """Release only if we still hold our own token.

        Checked because a lock that expired and was retaken by someone else
        must not be released by us; deleting it then would hand a third
        process a lock two others believe they hold.
        """
        if self.state != ACQUIRED:
            return
        try:
            from app.redis_cache import redis_client, REDIS_AVAILABLE

            if not REDIS_AVAILABLE:
                return
            if redis_client.get(self.key) in (self.token, self.token.encode()):
                redis_client.delete(self.key)
        except Exception as e:
            logger.warning("strict lock release failed for %s: %s", self.key, e)
        finally:
            self.state = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


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
