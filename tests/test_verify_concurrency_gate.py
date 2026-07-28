"""Bounded concurrency in front of password verification.

The rate limiter bounds attempts per key per window, not how many are running
at once. A burst spread across distinct addresses and accounts stays inside
every budget while starting arbitrarily many Argon2 verifications together, and
N of those hold roughly N x memory_cost. The gate is what makes N finite.
"""
import threading
import time

import pytest
from fastapi import HTTPException

from app.auth import (
    VERIFY_CONCURRENCY,
    _hash_password,
    _verify_slots,
    authenticate,
)

PASSWORD = "correct horse battery staple"


def user_with(hashed: str) -> dict:
    return {"username": "u", "hashed_password": hashed, "role": "viewer"}


def drain_slots(n: int):
    """Hold n slots so the next verification finds the process saturated."""
    for _ in range(n):
        assert _verify_slots.acquire(blocking=False)


def release_slots(n: int):
    for _ in range(n):
        _verify_slots.release()


def test_the_configured_bound_is_sane():
    assert 1 <= VERIFY_CONCURRENCY <= 16


def test_a_saturated_process_sheds_the_request():
    """Queueing would turn memory pressure into latency for everyone."""
    users = {"u": user_with(_hash_password(PASSWORD))}
    drain_slots(VERIFY_CONCURRENCY)
    try:
        with pytest.raises(HTTPException) as excinfo:
            authenticate("u", PASSWORD, users)
        assert excinfo.value.status_code == 503
        assert int(excinfo.value.headers["Retry-After"]) > 0
    finally:
        release_slots(VERIFY_CONCURRENCY)


def test_an_unknown_account_is_shed_identically():
    """Otherwise saturation behaviour tells an attacker which accounts exist."""
    drain_slots(VERIFY_CONCURRENCY)
    try:
        with pytest.raises(HTTPException) as known:
            authenticate("u", PASSWORD, {"u": user_with(_hash_password(PASSWORD))})
        with pytest.raises(HTTPException) as unknown:
            authenticate("nobody", PASSWORD, {})
        assert known.value.status_code == unknown.value.status_code
        assert known.value.detail == unknown.value.detail
    finally:
        release_slots(VERIFY_CONCURRENCY)


@pytest.mark.parametrize("password,users", [
    (PASSWORD, {"u": None}),          # success
    ("wrong", {"u": None}),           # wrong password
    (PASSWORD, {}),                   # unknown account
])
def test_the_slot_is_returned_on_every_path(password, users):
    if "u" in users:
        users = {"u": user_with(_hash_password(PASSWORD))}
    before = _slots_free()

    authenticate("u", password, users)

    assert _slots_free() == before, "a slot leaked"


def test_the_slot_is_returned_when_verification_raises(monkeypatch):
    import app.auth as auth_module

    before = _slots_free()

    def boom(*a, **kw):
        raise ValueError("hash backend exploded")

    monkeypatch.setattr(auth_module, "verify_password", boom)
    with pytest.raises(ValueError):
        authenticate("u", PASSWORD, {"u": user_with(_hash_password(PASSWORD))})

    assert _slots_free() == before, "a slot leaked on the exception path"


def test_no_more_than_the_bound_run_at_once():
    """Measured on the real gate rather than inferred from the semaphore."""
    users = {f"u{i}": user_with(_hash_password(PASSWORD)) for i in range(16)}
    peak = 0
    live = 0
    lock = threading.Lock()
    import app.auth as auth_module
    real_verify = auth_module.verify_password

    def counting_verify(plain, stored):
        nonlocal peak, live
        with lock:
            live += 1
            peak = max(peak, live)
        try:
            time.sleep(0.02)
            return real_verify(plain, stored)
        finally:
            with lock:
                live -= 1

    auth_module.verify_password = counting_verify
    try:
        threads = [
            threading.Thread(target=lambda i=i: _swallow(users, f"u{i}"))
            for i in range(16)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        auth_module.verify_password = real_verify

    assert peak <= VERIFY_CONCURRENCY, f"{peak} verifications ran at once"


def _swallow(users, name):
    try:
        authenticate(name, PASSWORD, users)
    except HTTPException:
        pass


def _slots_free() -> int:
    got = 0
    while _verify_slots.acquire(blocking=False):
        got += 1
    for _ in range(got):
        _verify_slots.release()
    return got


def test_an_out_of_range_concurrency_is_refused(monkeypatch):
    from app.auth import _verify_concurrency

    monkeypatch.setenv("SORA_PASSWORD_VERIFY_CONCURRENCY", "0")
    with pytest.raises(RuntimeError, match="outside 1..16"):
        _verify_concurrency()

    monkeypatch.setenv("SORA_PASSWORD_VERIFY_CONCURRENCY", "17")
    with pytest.raises(RuntimeError, match="outside 1..16"):
        _verify_concurrency()

    monkeypatch.setenv("SORA_PASSWORD_VERIFY_CONCURRENCY", "many")
    with pytest.raises(RuntimeError, match="must be an integer"):
        _verify_concurrency()
