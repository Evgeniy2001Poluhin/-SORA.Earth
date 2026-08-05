"""A lock that cannot say "I don't know" is not a lock for a mass write path.

`RedisLock.acquire()` returns True when it took the lock and also when Redis is
unreachable or the call raised. That fail-open default suits jobs where a
duplicate run is harmless, and it is wrong here: it made the claim "a manual
and a scheduled history refresh cannot overlap" untrue exactly when Redis is
down, which is when both are most likely to be retried by hand.

`StrictLock` reports acquired / held / unavailable, and the callers refuse on
the last two. These tests pin all three outcomes, and the refusal of each
caller, because the interesting one -- unavailable -- is the one no test
naturally produces.
"""

import sys
import time
import types

import pytest

from app.locks import ACQUIRED, HELD, UNAVAILABLE, StrictLock


class _FakeRedis:
    """Enough Redis to exercise the lock's lifecycle, including EVAL.

    `eval` interprets the two scripts the lock uses. What that demonstrates is
    the compare-and-act *logic* -- that a release matching the wrong token
    removes nothing, and a renewal on a lost lease reports failure. The
    atomicity itself rests on Redis executing a script without interleaving,
    which is a documented property of the server and not something a fake can
    prove.

    `expire_lease()` stands in for a TTL elapsing, so the interesting sequence
    -- lease expires, someone else takes the key, the first owner releases --
    can be produced deterministically rather than by waiting.
    """

    def __init__(self, existing=None, raises=False):
        self.store = dict(existing or {})
        self.ttl = {}
        self.raises = raises
        self.evals = []

    def set(self, key, token, nx=False, ex=None):
        if self.raises:
            raise ConnectionError("redis is gone")
        if nx and key in self.store:
            return None
        self.store[key] = token
        if ex is not None:
            self.ttl[key] = ex
        return True

    def get(self, key):
        if self.raises:
            raise ConnectionError("redis is gone")
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)
        self.ttl.pop(key, None)

    def expire_lease(self, key):
        """The lease elapsed: the key is gone, as Redis would have it."""
        self.delete(key)

    def eval(self, script, numkeys, *args):
        if self.raises:
            raise ConnectionError("redis is gone")
        key = args[0]
        token = args[1]
        self.evals.append((script.strip().splitlines()[1].strip(), key, token))

        if 'del' in script:
            if self.store.get(key) == token:
                self.delete(key)
                return 1
            return 0
        if 'expire' in script:
            if self.store.get(key) == token:
                self.ttl[key] = int(args[2])
                return 1
            return 0
        raise AssertionError(f"unexpected script: {script}")


@pytest.fixture
def redis_module(monkeypatch):
    """Stand in for app.redis_cache, which the lock imports at call time."""
    def install(available=True, existing=None, raises=False):
        module = types.ModuleType("app.redis_cache")
        module.REDIS_AVAILABLE = available
        module.redis_client = _FakeRedis(existing, raises)
        monkeypatch.setitem(sys.modules, "app.redis_cache", module)
        return module

    return install


def test_a_free_lock_is_acquired(redis_module):
    redis_module(available=True)
    assert StrictLock("k").acquire() == ACQUIRED


def test_a_taken_lock_is_held(redis_module):
    redis_module(available=True, existing={"k": "someone-else"})
    assert StrictLock("k").acquire() == HELD


def test_redis_switched_off_is_unavailable_not_free(redis_module):
    """The case RedisLock reports as success."""
    redis_module(available=False)
    assert StrictLock("k").acquire() == UNAVAILABLE


def test_a_raising_redis_is_unavailable_not_free(redis_module):
    """An exception is not evidence that nobody holds the lock."""
    redis_module(available=True, raises=True)
    assert StrictLock("k").acquire() == UNAVAILABLE


def test_release_after_the_lease_expired_and_was_retaken(redis_module):
    """The sequence a GET-then-DELETE release gets wrong.

        A acquires
        A's lease expires
        B acquires the same key
        A.release()
        -> B still holds it

    With GET and DELETE as separate calls the lease can lapse between them, so
    A deletes a lock it no longer owns and B keeps working while a third
    process takes the key. Compare-and-delete in one script cannot do that.
    """
    module = redis_module(available=True)
    a = StrictLock("k", timeout=1)
    assert a.acquire() == ACQUIRED

    b = StrictLock("k", timeout=60)

    # The interleaving is the whole point, and an earlier version of this test
    # missed it: expiring the lease *before* release lets even a GET-then-
    # DELETE implementation refuse correctly, because its comparison sees B's
    # token. The defect needs the lease to lapse *between* the two calls -- A
    # reads its own token, still valid, and deletes afterwards, by which time
    # the key is B's.
    #
    # So the expiry is triggered by the GET itself, once.
    # Both entry points are hooked, once, so the test discriminates rather
    # than merely reflecting which calls the current code happens to make:
    #
    #   GET + DELETE   the change lands *after* the read returns A's token,
    #                  so A deletes against a stale view -- the defect.
    #   one EVAL       the change lands *before* the script runs, so the
    #                  comparison inside it sees B's token and deletes
    #                  nothing. An atomic operation has no window to land in.
    fired = []
    original_get = module.redis_client.get
    original_eval = module.redis_client.eval

    def steal_the_lock():
        if fired:
            return
        fired.append(True)
        module.redis_client.expire_lease("k")
        assert b.acquire() == ACQUIRED

    def get_then_steal(key):
        value = original_get(key)
        steal_the_lock()
        return value

    def steal_then_eval(script, numkeys, *args):
        steal_the_lock()
        return original_eval(script, numkeys, *args)

    module.redis_client.get = get_then_steal
    module.redis_client.eval = steal_then_eval

    a.release()

    assert module.redis_client.store.get("k") == b.token, (
        "the previous owner deleted the new owner's lock"
    )


def test_renewal_keeps_the_lease_past_its_original_ttl(redis_module):
    """A run longer than the lease must not become two runs.

    Without renewal the key simply expires mid-run, a second process acquires
    it, and both work at once -- with neither able to tell.
    """
    module = redis_module(available=True)
    holder = StrictLock("k", timeout=3, renew=True)
    assert holder.acquire() == ACQUIRED

    # Two renewal intervals (timeout/3, floored at 1s) plus slack.
    time.sleep(2.5)

    assert not holder.lost.is_set()
    assert StrictLock("k").acquire() == HELD, (
        "the lease lapsed while its holder was still working"
    )
    holder.release()


def test_losing_the_lease_is_noticed_and_signalled(redis_module):
    """Renewal on a lease someone else holds must fail, and say so.

    The holder cannot simply carry on: it no longer has the exclusivity the
    work depends on, and nothing else will tell it.
    """
    module = redis_module(available=True)
    holder = StrictLock("k", timeout=3, renew=True)
    assert holder.acquire() == ACQUIRED

    module.redis_client.store["k"] = "someone-elses-token"

    assert holder.lost.wait(timeout=5), "losing the lease was not signalled"
    assert holder.renew_once() is False

    holder.release()
    assert module.redis_client.store["k"] == "someone-elses-token", (
        "release removed the new owner's lock"
    )


def test_release_stops_and_joins_the_renewal_thread(redis_module):
    """A daemon thread left running would keep renewing a released lock."""
    redis_module(available=True)
    lock = StrictLock("k", timeout=3, renew=True)
    assert lock.acquire() == ACQUIRED
    renewer = lock._renewer
    assert renewer is not None and renewer.is_alive()

    lock.release()

    assert not renewer.is_alive(), "the renewal thread outlived the lock"


def test_release_only_removes_our_own_token(redis_module):
    """A lock that expired and was retaken must not be released by us.

    Deleting it then hands a third process a lock two others believe they
    hold, which is worse than not releasing at all.
    """
    module = redis_module(available=True)
    lock = StrictLock("k")
    assert lock.acquire() == ACQUIRED

    module.redis_client.store["k"] = "someone-elses-token"
    lock.release()

    assert module.redis_client.store["k"] == "someone-elses-token"


def test_release_after_a_failed_acquire_does_nothing(redis_module):
    """Releasing a lock we never held would free someone else's."""
    module = redis_module(available=True, existing={"k": "theirs"})
    lock = StrictLock("k")
    assert lock.acquire() == HELD

    lock.release()

    assert module.redis_client.store["k"] == "theirs"


def test_the_context_manager_releases(redis_module):
    module = redis_module(available=True)
    with StrictLock("k") as lock:
        assert lock.state == ACQUIRED
    assert "k" not in module.redis_client.store


def test_the_context_manager_releases_on_an_exception(redis_module):
    """finally, not a happy path -- a raised refresh must not keep the lock."""
    module = redis_module(available=True)
    with pytest.raises(ValueError):
        with StrictLock("k"):
            raise ValueError("the refresh failed")
    assert "k" not in module.redis_client.store


def test_the_scheduled_refresh_skips_when_the_lock_is_unreachable(
    monkeypatch, redis_module,
):
    """Fail closed: no lock means no mass write, and a recorded reason."""
    from unittest.mock import MagicMock

    import app.external_data as ed

    redis_module(available=False)
    calls = []
    monkeypatch.setattr(ed, "SessionLocal", lambda: MagicMock())
    monkeypatch.setattr(ed, "DataRefreshLog", MagicMock())
    monkeypatch.setattr(ed, "refresh_all_countries",
                        lambda: {"fetched": 1, "total": 1, "countries": {}})
    monkeypatch.setattr(ed, "refresh_indicator_history",
                        lambda **kw: calls.append(kw) or {})
    monkeypatch.setenv("SORA_HISTORY_REFRESH", "on")

    ed.refresh_live_data(trigger_source="test")

    assert calls == [], "a mass write started without an assured lock"


def test_the_scheduled_refresh_skips_when_the_lock_is_held(
    monkeypatch, redis_module,
):
    from unittest.mock import MagicMock

    import app.external_data as ed

    redis_module(available=True, existing={ed.HISTORY_LOCK_KEY: "manual-run"})
    calls = []
    monkeypatch.setattr(ed, "SessionLocal", lambda: MagicMock())
    monkeypatch.setattr(ed, "DataRefreshLog", MagicMock())
    monkeypatch.setattr(ed, "refresh_all_countries",
                        lambda: {"fetched": 1, "total": 1, "countries": {}})
    monkeypatch.setattr(ed, "refresh_indicator_history",
                        lambda **kw: calls.append(kw) or {})
    monkeypatch.setenv("SORA_HISTORY_REFRESH", "on")

    ed.refresh_live_data(trigger_source="test")

    assert calls == [], "the scheduled run started while a manual one held the lock"


def test_the_scheduled_refresh_runs_when_the_lock_is_free(
    monkeypatch, redis_module,
):
    """And it is not simply a permanent refusal."""
    from unittest.mock import MagicMock

    import app.external_data as ed

    redis_module(available=True)
    calls = []
    monkeypatch.setattr(ed, "SessionLocal", lambda: MagicMock())
    monkeypatch.setattr(ed, "DataRefreshLog", MagicMock())
    monkeypatch.setattr(ed, "refresh_all_countries",
                        lambda: {"fetched": 1, "total": 1, "countries": {}})
    monkeypatch.setattr(
        ed, "refresh_indicator_history",
        lambda **kw: calls.append(kw) or {
            "fetched": 0, "inserted": 0, "unchanged": 0,
            "revised": 0, "no_value": 0, "no_period": 0,
        },
    )
    monkeypatch.setenv("SORA_HISTORY_REFRESH", "on")

    ed.refresh_live_data(trigger_source="test")

    assert len(calls) == 1
