"""The login limiter's key space, proxy handling and cost bounds.

Argon2 makes login expensive on purpose, so the limiter in front of it is
load-bearing. These pin the parts that are easy to get wrong: keys derived from
attacker-supplied input must not grow without bound, a proxy's address must not
become everyone's identity, and a mistyped cost must not be accepted in either
direction.
"""
import time
import types

import pytest
from fastapi import HTTPException

from app.auth import ARGON2_CEILING, ARGON2_FLOOR, _account_key, _argon2_params
from app.rate_limit import RateLimiter, client_address


def request_from(peer: str | None, headers: dict | None = None):
    return types.SimpleNamespace(
        client=types.SimpleNamespace(host=peer) if peer else None,
        headers=headers or {},
    )


# ------------------------------------------------------------- key space bound


def test_buckets_are_dropped_once_they_age_out():
    """Retirement is incremental by design: a full scan per request is the
    cost the bound exists to avoid. So the table drains over successive calls
    rather than in one, and what matters is that it drains at all."""
    limiter = RateLimiter(max_requests=5, window_seconds=1)
    for i in range(50):
        limiter.check(f"k{i}")
    assert len(limiter.requests) == 50

    time.sleep(1.05)
    for i in range(20):
        limiter.check(f"live{i}")

    stale = [k for k in limiter.requests if k.startswith("k")]
    assert not stale, f"{len(stale)} aged-out buckets were never released"


def test_the_table_is_capped_against_a_cardinality_flood():
    """A fresh username per request must not buy a fresh bucket per request."""
    limiter = RateLimiter(max_requests=5, window_seconds=60, max_buckets=100)

    for i in range(5_000):
        limiter.check(f"login-user:{i}")

    assert len(limiter.requests) <= 100


def test_an_arbitrarily_long_username_yields_a_fixed_width_key():
    assert len(_account_key("a" * 10_000)) == 32
    assert len(_account_key("")) == 32


def test_case_and_padding_share_one_budget():
    """Otherwise trivial variations each buy a fresh allowance for one account."""
    assert _account_key(" Admin ") == _account_key("admin")
    assert _account_key("admin") != _account_key("administrator")


def test_the_raw_username_does_not_appear_in_the_key():
    assert "hunter" not in _account_key("hunter")


# --------------------------------------------------------------- proxy trust


def test_an_untrusted_peer_cannot_claim_another_address():
    """Believing the header unconditionally would let anyone reset their budget."""
    request = request_from("203.0.113.9", {"x-real-ip": "10.0.0.1"})
    assert client_address(request) == "203.0.113.9"


def test_a_trusted_proxy_reports_the_real_caller():
    """Behind nginx every request otherwise shares one bucket."""
    request = request_from("172.18.0.5", {"x-real-ip": "198.51.100.7"})
    assert client_address(request) == "198.51.100.7"


def test_a_trusted_proxy_with_no_header_falls_back_to_the_peer():
    assert client_address(request_from("127.0.0.1", {})) == "127.0.0.1"


def test_a_trusted_proxy_reporting_garbage_falls_back_to_the_peer():
    request = request_from("127.0.0.1", {"x-real-ip": "not-an-address"})
    assert client_address(request) == "127.0.0.1"


def test_a_missing_client_is_not_an_error():
    assert client_address(request_from(None)) == "unknown"


# ------------------------------------------------------------ 429 contract


def test_the_rejection_carries_retry_after():
    limiter = RateLimiter(max_requests=1, window_seconds=42)
    limiter.check("k")

    with pytest.raises(HTTPException) as excinfo:
        limiter.check("k")

    assert excinfo.value.status_code == 429
    assert excinfo.value.headers["Retry-After"] == "42"


def test_the_rejection_does_not_say_which_key_was_exhausted():
    """The response must not distinguish an exhausted address from an account."""
    limiter = RateLimiter(max_requests=1, window_seconds=60)
    limiter.check("login-ip:203.0.113.1")
    limiter.check("login-user:abc")

    with pytest.raises(HTTPException) as by_ip:
        limiter.check("login-ip:203.0.113.1")
    with pytest.raises(HTTPException) as by_user:
        limiter.check("login-user:abc")

    assert by_ip.value.detail == by_user.value.detail
    assert "203.0.113.1" not in str(by_ip.value.detail)
    assert "abc" not in str(by_user.value.detail)


# --------------------------------------------------------------- cost bounds


def test_a_cost_above_the_ceiling_is_refused(monkeypatch):
    """A mistyped value denies service as surely as a weak one."""
    monkeypatch.setenv("SORA_ARGON2_MEMORY_COST", str(ARGON2_CEILING["memory_cost"] + 1))
    with pytest.raises(RuntimeError, match="denies service"):
        _argon2_params()


def test_the_ceiling_itself_is_allowed(monkeypatch):
    monkeypatch.setenv("SORA_ARGON2_TIME_COST", str(ARGON2_CEILING["time_cost"]))
    assert _argon2_params()["time_cost"] == ARGON2_CEILING["time_cost"]


def test_the_defaults_sit_inside_both_bounds():
    params = _argon2_params()
    for name, floor in ARGON2_FLOOR.items():
        assert floor <= params[name] <= ARGON2_CEILING[name]


# ------------------------------------------------ cost of the bound itself


def test_cleanup_inspects_a_bounded_number_of_buckets():
    """The memory cap must not become a CPU amplifier.

    Scanning or sorting the table per request would mean every caller pays for
    the whole table once an attacker has filled it, and filling it is cheap. The
    count is asserted rather than the wall time: timing comparisons are flaky,
    the number of buckets examined is exact.
    """
    limiter = RateLimiter(max_requests=5, window_seconds=60, max_buckets=5_000)
    for i in range(5_000):
        limiter.check(f"warm{i}")

    limiter.inspected = 0
    for i in range(100):
        limiter.check(f"more{i}")

    assert limiter.inspected <= 100 * RateLimiter.SWEEP_PER_CALL, (
        f"{limiter.inspected} buckets examined over 100 calls against a table "
        f"of {len(limiter.requests)} -- cleanup is not bounded"
    )


def test_no_full_table_scan_or_sort_remains():
    """Structural, so a future edit cannot quietly reintroduce the cost."""
    import inspect

    from app import rate_limit

    source = inspect.getsource(rate_limit.RateLimiter)
    assert "sorted(" not in source, "a sort over the table is back"
    assert "for k, hits in self.requests.items()" not in source


def test_a_hundred_thousand_distinct_keys_stay_bounded():
    limiter = RateLimiter(max_requests=5, window_seconds=60, max_buckets=1_000)
    for i in range(100_000):
        limiter.check(f"u{i}")
    assert len(limiter.requests) <= 1_000


def test_recent_callers_survive_a_flood_of_single_use_keys():
    """Eviction takes the least recently seen, which is what a flood looks like."""
    limiter = RateLimiter(max_requests=10_000, window_seconds=60, max_buckets=100)
    limiter.check("regular-caller")

    for i in range(1_000):
        limiter.check(f"throwaway{i}")
        limiter.check("regular-caller")

    assert "regular-caller" in limiter.requests, \
        "a returning caller was evicted ahead of 1000 single-use keys"
    assert len(limiter.requests) <= 100


def test_an_exhausted_address_creates_no_account_buckets():
    """Otherwise one address could mint the whole table before being stopped."""
    from app.auth import _login_limiter, authenticate

    _login_limiter.requests.clear()
    ip = "203.0.113.200"
    users = {}

    for i in range(_login_limiter.max_requests):
        authenticate(f"name{i}", "x", users, client_ip=ip)
    buckets_after_budget = len(_login_limiter.requests)

    for i in range(500):
        try:
            authenticate(f"blocked{i}", "x", users, client_ip=ip)
        except HTTPException:
            pass

    assert len(_login_limiter.requests) == buckets_after_budget, \
        "rejected requests still created account buckets"
    _login_limiter.requests.clear()


def test_the_clock_is_monotonic():
    """A wall-clock adjustment would either free every budget or freeze them."""
    import inspect

    from app import rate_limit

    source = inspect.getsource(rate_limit.RateLimiter)
    assert "time.monotonic()" in source
    assert "time.time()" not in source


def test_the_lock_is_not_held_during_password_verification():
    """Argon2 takes ~130 ms. Holding the limiter lock across it would serialise
    every login in the process behind one verification."""
    import inspect

    from app import auth

    source = inspect.getsource(auth.authenticate)
    assert source.index("_login_limiter.check") < source.index("_verify_and_upgrade_under_gate"), \
        "the rate limit must be spent before the expensive work, not after"
    assert "with _login_limiter" not in source, "the lock must not span the verify"

    # The verification itself must not reach for the limiter at all.
    gate = inspect.getsource(auth._verify_and_upgrade_under_gate)
    assert "_login_limiter" not in gate
    assert "_lock" not in gate


def test_concurrent_checks_cannot_overspend_one_bucket():
    """check() is a read-modify-write; the GIL makes each step atomic, not the
    sequence."""
    import threading

    limiter = RateLimiter(max_requests=50, window_seconds=60)
    allowed = []
    barrier = threading.Barrier(8)

    def hammer():
        barrier.wait()
        for _ in range(20):
            try:
                limiter.check("shared")
                allowed.append(1)
            except HTTPException:
                pass

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(allowed) == 50, f"{len(allowed)} calls admitted against a budget of 50"


def test_retry_after_is_a_positive_whole_number_of_seconds():
    limiter = RateLimiter(max_requests=1, window_seconds=42)
    limiter.check("k")
    with pytest.raises(HTTPException) as excinfo:
        limiter.check("k")

    value = excinfo.value.headers["Retry-After"]
    assert value.isdigit() and int(value) > 0


def test_the_account_key_is_keyed_not_a_bare_digest():
    """A plain digest of a username is reversible by dictionary."""
    import hashlib

    from app.auth import _account_key

    bare = hashlib.sha256("admin".encode()).hexdigest()[:32]
    assert _account_key("admin") != bare
