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


def test_a_saturated_table_does_not_get_slower():
    """The memory cap must not become a CPU amplifier.

    Scanning or sorting the whole table per request would mean every caller
    pays for the table once an attacker has filled it. Filling it is cheap for
    the attacker, so that trade has to not exist.
    """
    limiter = RateLimiter(max_requests=5, window_seconds=60, max_buckets=2_000)

    empty = time.perf_counter()
    for i in range(2_000):
        limiter.check(f"warm{i}")
    fill = time.perf_counter() - empty

    saturated = time.perf_counter()
    for i in range(2_000):
        limiter.check(f"flood{i}")
    after = time.perf_counter() - saturated

    assert len(limiter.requests) <= 2_000
    # Linear behaviour would make the second pass several times the first.
    assert after < fill * 3, f"saturated inserts cost {after:.3f}s vs {fill:.3f}s empty"


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
