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


def set_hash(user_id, value: str) -> None:
    import sqlalchemy as sa
    from app.database import SessionLocal, User
    with SessionLocal() as session:
        session.execute(sa.update(User).where(User.id == user_id).values(password_hash=value))
        session.commit()


def drain_slots(n: int):
    """Hold n slots so the next verification finds the process saturated."""
    for _ in range(n):
        assert _verify_slots.acquire(blocking=False)


def release_slots(n: int):
    for _ in range(n):
        _verify_slots.release()


def test_the_configured_bound_is_sane():
    assert 1 <= VERIFY_CONCURRENCY <= 16


def test_a_saturated_process_sheds_the_request(make_account):
    """Queueing would turn memory pressure into latency for everyone."""
    user = make_account()
    drain_slots(VERIFY_CONCURRENCY)
    try:
        with pytest.raises(HTTPException) as excinfo:
            authenticate(user.username, PASSWORD)
        assert excinfo.value.status_code == 503
        assert int(excinfo.value.headers["Retry-After"]) > 0
    finally:
        release_slots(VERIFY_CONCURRENCY)


def test_an_unknown_account_is_shed_identically(make_account):
    """Otherwise saturation behaviour tells an attacker which accounts exist."""
    user = make_account()
    drain_slots(VERIFY_CONCURRENCY)
    try:
        with pytest.raises(HTTPException) as known:
            authenticate(user.username, PASSWORD)
        with pytest.raises(HTTPException) as unknown:
            authenticate("nobody-at-all", PASSWORD)
        assert known.value.status_code == unknown.value.status_code
        assert known.value.detail == unknown.value.detail
    finally:
        release_slots(VERIFY_CONCURRENCY)


@pytest.mark.parametrize("password,exists", [
    (PASSWORD, True),          # success
    ("wrong", True),           # wrong password
    (PASSWORD, False),         # unknown account
])
def test_the_slot_is_returned_on_every_path(make_account, password, exists):
    name = make_account().username if exists else "nobody-at-all"
    before = _slots_free()

    authenticate(name, password)

    assert _slots_free() == before, "a slot leaked"


def test_the_slot_is_returned_when_verification_raises(monkeypatch, make_account):
    import app.auth as auth_module

    user = make_account()
    before = _slots_free()

    def boom(*a, **kw):
        raise ValueError("hash backend exploded")

    monkeypatch.setattr(auth_module, "verify_password", boom)
    with pytest.raises(ValueError):
        authenticate(user.username, PASSWORD)

    assert _slots_free() == before, "a slot leaked on the exception path"


def test_no_more_than_the_bound_run_at_once(make_account):
    """Measured on the real gate rather than inferred from the semaphore."""
    users = [make_account().username for _ in range(16)]
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
            threading.Thread(target=lambda i=i: _swallow(users[i]))
            for i in range(16)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        auth_module.verify_password = real_verify

    assert peak <= VERIFY_CONCURRENCY, f"{peak} verifications ran at once"


def _swallow(name):
    try:
        authenticate(name, PASSWORD)
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

    gate = inspect.getsource(auth._verify_under_gate)
    assert "_password_hasher.hash" not in gate


def test_the_absent_account_hash_is_stable_and_valid():
    first = _ABSENT_ACCOUNT_HASH
    authenticate("nobody-at-all", "x")
    from app.auth import _ABSENT_ACCOUNT_HASH as second

    assert first == second, "the dummy hash was rebuilt"
    assert first.startswith("$argon2id$")


def test_an_unknown_account_costs_exactly_one_verification(monkeypatch):
    import app.auth as auth_module

    calls = []
    real = auth_module.verify_password
    monkeypatch.setattr(auth_module, "verify_password",
                        lambda p, h: (calls.append(h), real(p, h))[1])

    authenticate("nobody-at-all", "x")

    assert len(calls) == 1, f"{len(calls)} verifications for one unknown user"
    assert calls[0] == _ABSENT_ACCOUNT_HASH


def test_verify_and_rehash_share_one_slot():
    """Releasing between them would put one login through the gate twice."""
    import inspect

    from app import auth

    gate = inspect.getsource(auth._verify_under_gate)
    assert gate.count("_verify_slots.acquire") == 1
    assert gate.count("_verify_slots.release") == 1
    assert "_hash_password(plain)" in gate, "the rehash must be computed inside the slot"

    caller = inspect.getsource(auth.authenticate)
    assert "_verify_slots" not in caller, "the caller must not take a second slot"


def test_a_damaged_stored_hash_fails_the_login_and_is_reported(caplog, make_account):
    """Folding it into bad credentials would hide a corrupt record until
    somebody noticed they could never log in."""
    import logging

    user = make_account()
    set_hash(user.id, "$argon2id$v=19$m=65536,t=3,p=4$not-real-base64$xx")

    with caplog.at_level(logging.ERROR, logger="sora_earth"):
        assert authenticate(user.username, PASSWORD) is None

    assert any("could not be evaluated" in r.message for r in caplog.records)
    for record in caplog.records:
        assert PASSWORD not in record.getMessage()
        assert "argon2id" not in record.getMessage()


def test_a_wrong_password_is_not_reported_as_a_backend_failure(caplog, make_account):
    import logging

    user = make_account()

    with caplog.at_level(logging.ERROR, logger="sora_earth"):
        assert authenticate(user.username, "wrong") is None

    assert not any("could not be evaluated" in r.message for r in caplog.records)
