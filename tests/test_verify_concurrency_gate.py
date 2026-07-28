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
    _ABSENT_ACCOUNT_HASH,
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


# -------------------------------------------------- lifetimes and error paths


def test_the_absent_account_hash_is_built_once_at_import():
    """Rebuilding it per request would make an unknown account cost a hash and
    a verify -- a cheaper way to burn CPU than a real login."""
    import inspect

    from app import auth

    source = inspect.getsource(auth.authenticate)
    assert "_password_hasher.hash" not in source
    assert "_ABSENT_ACCOUNT_HASH" in source

    gate = inspect.getsource(auth._verify_and_upgrade_under_gate)
    assert "_password_hasher.hash" not in gate


def test_the_absent_account_hash_is_stable_and_valid():
    first = _ABSENT_ACCOUNT_HASH
    authenticate("nobody", "x", {})
    from app.auth import _ABSENT_ACCOUNT_HASH as second

    assert first == second, "the dummy hash was rebuilt"
    assert first.startswith("$argon2id$")


def test_an_unknown_account_costs_exactly_one_verification(monkeypatch):
    import app.auth as auth_module

    calls = []
    real = auth_module.verify_password
    monkeypatch.setattr(auth_module, "verify_password",
                        lambda p, h: (calls.append(h), real(p, h))[1])

    authenticate("nobody-at-all", "x", {})

    assert len(calls) == 1, f"{len(calls)} verifications for one unknown user"
    assert calls[0] == _ABSENT_ACCOUNT_HASH


def test_verify_and_rehash_share_one_slot():
    """Releasing between them would put one login through the gate twice."""
    import inspect

    from app import auth

    gate = inspect.getsource(auth._verify_and_upgrade_under_gate)
    assert gate.count("_verify_slots.acquire") == 1
    assert gate.count("_verify_slots.release") == 1
    assert "upgrade_password_hash" in gate, "the rehash must happen inside the slot"

    caller = inspect.getsource(auth.authenticate)
    assert "_verify_slots" not in caller, "the caller must not take a second slot"


def test_a_damaged_stored_hash_fails_the_login_and_is_reported(caplog):
    """Folding it into bad credentials would hide a corrupt record until
    somebody noticed they could never log in."""
    import logging

    users = {"u": user_with("$argon2id$v=19$m=65536,t=3,p=4$not-real-base64$xx")}

    with caplog.at_level(logging.ERROR, logger="sora_earth"):
        assert authenticate("u", PASSWORD, users) is None

    assert any("could not be evaluated" in r.message for r in caplog.records)
    for record in caplog.records:
        assert PASSWORD not in record.getMessage()
        assert "argon2id" not in record.getMessage()


def test_a_wrong_password_is_not_reported_as_a_backend_failure(caplog):
    import logging

    users = {"u": user_with(_hash_password(PASSWORD))}

    with caplog.at_level(logging.ERROR, logger="sora_earth"):
        assert authenticate("u", "wrong", users) is None

    assert not any("could not be evaluated" in r.message for r in caplog.records)
