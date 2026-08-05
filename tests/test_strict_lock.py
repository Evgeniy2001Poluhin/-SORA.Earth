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
import types

import pytest

from app.locks import ACQUIRED, HELD, UNAVAILABLE, StrictLock


class _FakeRedis:
    def __init__(self, existing=None, raises=False):
        self.store = dict(existing or {})
        self.raises = raises

    def set(self, key, token, nx=False, ex=None):
        if self.raises:
            raise ConnectionError("redis is gone")
        if nx and key in self.store:
            return None
        self.store[key] = token
        return True

    def get(self, key):
        if self.raises:
            raise ConnectionError("redis is gone")
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)


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
