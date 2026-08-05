import threading
import time
import uuid
import logging

logger = logging.getLogger(__name__)


ACQUIRED = "acquired"
HELD = "held"
UNAVAILABLE = "unavailable"

# Compare-and-delete in one step. A GET followed by a DELETE is two round
# trips, and the lease can expire between them: another process takes the same
# key, and the first owner's DELETE removes a lock it no longer holds. Then two
# processes believe they are alone.
_RELEASE_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""

# Same shape for renewal: extend only a lease we still own. Renewing by key
# alone would let a process that has already lost the lock keep pushing out
# someone else's expiry.
_RENEW_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
end
return 0
"""


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

    def __init__(self, key: str, timeout: int = 300, renew: bool = False):
        self.key = key
        self.timeout = timeout
        self.token = str(uuid.uuid4())
        self.state = None
        self.renew = renew
        # Set when the lease is found to belong to someone else, or could not
        # be renewed. A holder that has lost the lock must stop doing the work
        # the lock was protecting -- silently carrying on is the failure the
        # lock exists to prevent.
        self.lost = threading.Event()
        self._stop = threading.Event()
        self._renewer = None

    def acquire(self) -> str:
        try:
            from app.redis_cache import redis_client, REDIS_AVAILABLE

            if not REDIS_AVAILABLE:
                self.state = UNAVAILABLE
                return UNAVAILABLE
            got = bool(redis_client.set(
                self.key, self.token, nx=True, ex=self.timeout))
            self.state = ACQUIRED if got else HELD
            if self.state == ACQUIRED and self.renew:
                self._start_renewal()
            return self.state
        except Exception as e:
            # An exception is not evidence that nobody holds the lock, so it
            # cannot be reported as free.
            logger.warning("strict lock unavailable for %s: %s", self.key, e)
            self.state = UNAVAILABLE
            return UNAVAILABLE

    def renew_once(self) -> bool:
        """Extend our own lease. False means we no longer hold it."""
        try:
            from app.redis_cache import redis_client, REDIS_AVAILABLE

            if not REDIS_AVAILABLE:
                return False
            extended = redis_client.eval(
                _RENEW_LUA, 1, self.key, self.token, self.timeout)
            return bool(extended)
        except Exception as e:
            logger.warning("strict lock renewal failed for %s: %s", self.key, e)
            return False

    def _start_renewal(self) -> None:
        """Refresh the lease every timeout/3 until told to stop.

        A third of the lease, so two consecutive failures still leave time to
        notice. Without this a run that outlives its lease keeps working while
        another process takes the key -- the exact overlap the lock exists to
        prevent, and the one nobody would see, since the first process never
        learns it lost.
        """
        interval = max(1.0, self.timeout / 3.0)

        def loop():
            while not self._stop.wait(interval):
                if not self.renew_once():
                    logger.error(
                        "strict lock %s is no longer ours; signalling the "
                        "holder to stop", self.key)
                    self.lost.set()
                    return

        self._renewer = threading.Thread(
            target=loop, name=f"lock-renew-{self.key}", daemon=True)
        self._renewer.start()

    def release(self) -> None:
        """Release only our own lease, atomically.

        Compare-and-delete in one Lua step rather than GET then DELETE: the
        lease can expire between those two calls, another process can take the
        same key, and the DELETE would then remove a lock we no longer hold --
        leaving two processes each believing they are alone.
        """
        self._stop.set()
        if self._renewer is not None:
            self._renewer.join(timeout=5)
            self._renewer = None

        if self.state != ACQUIRED:
            return
        try:
            from app.redis_cache import redis_client, REDIS_AVAILABLE

            if not REDIS_AVAILABLE:
                return
            redis_client.eval(_RELEASE_LUA, 1, self.key, self.token)
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
