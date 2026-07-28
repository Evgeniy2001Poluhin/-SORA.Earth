"""Password storage: Argon2id, with the two legacy formats migrating on login.

A single SHA-256 round is the wrong primitive for passwords whether or not it
is salted — commodity GPUs evaluate it in the billions per second — and the
older of the two legacy formats is not even salted. Both are still accepted so
that no account is locked out, and both are replaced the next time their owner
signs in. These tests pin that behaviour: that the new format is actually
Argon2id, that neither legacy format stops working, and that a successful login
leaves the stored hash upgraded.
"""
import hashlib

import pytest

from app.auth import (
    ARGON2_PREFIX,
    _hash_password,
    authenticate,
    legacy_hash_count,
    needs_rehash,
    upgrade_password_hash,
    verify_password,
)

PASSWORD = "correct horse battery staple"


def legacy_salted(password: str, salt: str = "a" * 32) -> str:
    """The format _hash_password produced before Argon2id."""
    return f"{salt}${hashlib.sha256(f'{salt}{password}'.encode()).hexdigest()}"


def legacy_unsalted(password: str) -> str:
    """The format before that — rainbow-table material."""
    return hashlib.sha256(password.encode()).hexdigest()


def user_with(hashed: str) -> dict:
    return {"username": "u", "hashed_password": hashed, "role": "viewer"}


# --------------------------------------------------------------- new format


def test_new_hashes_are_argon2id():
    assert _hash_password(PASSWORD).startswith("$argon2id$")


def test_new_hash_round_trips():
    assert verify_password(PASSWORD, _hash_password(PASSWORD))


def test_wrong_password_is_rejected():
    assert not verify_password("wrong", _hash_password(PASSWORD))


def test_each_hash_is_unique():
    """Distinct salts: identical passwords must not produce identical hashes."""
    assert _hash_password(PASSWORD) != _hash_password(PASSWORD)


def test_a_fresh_hash_does_not_need_rehashing():
    assert not needs_rehash(_hash_password(PASSWORD))


# ------------------------------------------------------------ legacy formats


def test_legacy_salted_still_verifies():
    assert verify_password(PASSWORD, legacy_salted(PASSWORD))


def test_legacy_unsalted_still_verifies():
    assert verify_password(PASSWORD, legacy_unsalted(PASSWORD))


def test_legacy_formats_reject_the_wrong_password():
    assert not verify_password("wrong", legacy_salted(PASSWORD))
    assert not verify_password("wrong", legacy_unsalted(PASSWORD))


@pytest.mark.parametrize("hashed", [legacy_salted(PASSWORD), legacy_unsalted(PASSWORD)])
def test_legacy_hashes_need_rehashing(hashed):
    assert needs_rehash(hashed)


@pytest.mark.parametrize("garbage", ["", "not-a-hash", "$argon2id$broken", "$", "a$b$c"])
def test_malformed_hashes_return_false_rather_than_raising(garbage):
    """A corrupt stored hash must fail the login, not 500 the endpoint."""
    assert verify_password(PASSWORD, garbage) is False


# ----------------------------------------------------------- upgrade on login


@pytest.mark.parametrize("legacy", [legacy_salted(PASSWORD), legacy_unsalted(PASSWORD)])
def test_authenticate_upgrades_a_legacy_hash_in_place(legacy):
    users = {"u": user_with(legacy)}

    assert authenticate("u", PASSWORD, users) is users["u"]

    stored = users["u"]["hashed_password"]
    assert stored.startswith(ARGON2_PREFIX), "the hash was not upgraded"
    assert stored != legacy
    assert verify_password(PASSWORD, stored), "the upgraded hash must still verify"


def test_authenticate_leaves_a_current_hash_alone():
    users = {"u": user_with(_hash_password(PASSWORD))}
    before = users["u"]["hashed_password"]

    authenticate("u", PASSWORD, users)

    assert users["u"]["hashed_password"] == before


def test_a_failed_login_does_not_upgrade_anything():
    """The plaintext is only known-good after verification; upgrading before
    that would let a wrong guess overwrite the stored hash."""
    legacy = legacy_salted(PASSWORD)
    users = {"u": user_with(legacy)}

    assert authenticate("u", "wrong", users) is None

    assert users["u"]["hashed_password"] == legacy


def test_authenticate_rejects_an_unknown_user():
    assert authenticate("nobody", PASSWORD, {}) is None


def test_upgrade_reports_whether_it_changed_anything():
    legacy = user_with(legacy_salted(PASSWORD))
    assert upgrade_password_hash(legacy, PASSWORD) is True
    assert upgrade_password_hash(legacy, PASSWORD) is False


# --------------------------------------------------------------- instrumentation


def test_legacy_hash_count_tracks_the_removal_condition():
    """The unsalted branch can only be deleted once this reaches zero."""
    users = {
        "a": user_with(legacy_unsalted(PASSWORD)),
        "b": user_with(legacy_salted(PASSWORD)),
        "c": user_with(_hash_password(PASSWORD)),
    }
    assert legacy_hash_count(users) == 2

    authenticate("a", PASSWORD, users)
    assert legacy_hash_count(users) == 1

    authenticate("b", PASSWORD, users)
    assert legacy_hash_count(users) == 0


def test_the_default_users_ship_on_the_current_format():
    from app.auth import USERS_DB

    assert legacy_hash_count(USERS_DB) == 0


# ------------------------------------------------------------------ end to end


@pytest.mark.parametrize("route,payload", [
    ("/api/v1/auth/login-json", {"username": "viewer", "password": "viewer123"}),
])
def test_login_over_http_upgrades_a_legacy_hash(client, route, payload):
    """The whole point, exercised through the endpoint rather than the helper."""
    from app.auth import USERS_DB

    original = USERS_DB["viewer"]["hashed_password"]
    USERS_DB["viewer"]["hashed_password"] = legacy_salted("viewer123")
    try:
        response = client.post(route, json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["access_token"]

        stored = USERS_DB["viewer"]["hashed_password"]
        assert stored.startswith(ARGON2_PREFIX), "login did not upgrade the stored hash"
        assert verify_password("viewer123", stored)
    finally:
        USERS_DB["viewer"]["hashed_password"] = original


# ------------------------------------------------- cost floor and DoS bounding


def test_the_cost_floor_cannot_be_configured_away(monkeypatch):
    """Raising the parameters is expected; lowering them past OWASP's floor is not.

    _argon2_params() is exercised directly rather than by reloading the module.
    A reload rebinds USERS_DB, the limiter and the hasher, while the route
    modules keep references to the previous objects — the two then disagree
    about which store is live, and the damage surfaces in an unrelated test
    later in the session.
    """
    from app.auth import ARGON2_FLOOR, _argon2_params

    monkeypatch.setenv("SORA_ARGON2_MEMORY_COST", str(ARGON2_FLOOR["memory_cost"] - 1))
    with pytest.raises(RuntimeError, match="Refusing to weaken password hashing"):
        _argon2_params()


def test_a_non_integer_cost_is_refused(monkeypatch):
    from app.auth import _argon2_params

    monkeypatch.setenv("SORA_ARGON2_TIME_COST", "lots")
    with pytest.raises(RuntimeError, match="must be an integer"):
        _argon2_params()


def test_raising_the_cost_is_allowed(monkeypatch):
    from app.auth import ARGON2_DEFAULTS, _argon2_params

    raised = ARGON2_DEFAULTS["time_cost"] + 1
    monkeypatch.setenv("SORA_ARGON2_TIME_COST", str(raised))
    assert _argon2_params()["time_cost"] == raised


def test_the_floor_is_exactly_at_the_boundary(monkeypatch):
    """At the floor is allowed; one below is not."""
    from app.auth import ARGON2_FLOOR, _argon2_params

    monkeypatch.setenv("SORA_ARGON2_MEMORY_COST", str(ARGON2_FLOOR["memory_cost"]))
    assert _argon2_params()["memory_cost"] == ARGON2_FLOOR["memory_cost"]


def test_defaults_apply_when_nothing_is_set(monkeypatch):
    from app.auth import ARGON2_DEFAULTS, ARGON2_FLOOR, _argon2_params

    for name in ARGON2_DEFAULTS:
        monkeypatch.delenv(f"SORA_ARGON2_{name.upper()}", raising=False)

    params = _argon2_params()
    assert params == ARGON2_DEFAULTS
    for name, floor in ARGON2_FLOOR.items():
        assert params[name] >= floor, f"the default for {name} is below its own floor"


def test_login_is_rate_limited_per_source_address():
    """Argon2 makes login expensive by design, so it has to be bounded.

    Without this, an unauthenticated caller can spend ~130 ms of CPU and 64 MiB
    per request with no ceiling.
    """
    from fastapi import HTTPException

    from app.auth import _login_limiter

    users = {"u": user_with(_hash_password(PASSWORD))}
    ip = "203.0.113.77"
    _login_limiter.requests.clear()

    for _ in range(_login_limiter.max_requests):
        authenticate("u", "wrong", users, client_ip=ip)

    with pytest.raises(HTTPException) as excinfo:
        authenticate("u", PASSWORD, users, client_ip=ip)
    assert excinfo.value.status_code == 429
    _login_limiter.requests.clear()


def test_the_budget_is_spent_per_account_as_well_as_per_address():
    """A distributed attempt on one account must also be bounded."""
    from fastapi import HTTPException

    from app.auth import _login_limiter

    users = {"target": user_with(_hash_password(PASSWORD))}
    _login_limiter.requests.clear()

    for i in range(_login_limiter.max_requests):
        authenticate("target", "wrong", users, client_ip=f"198.51.100.{i}")

    with pytest.raises(HTTPException) as excinfo:
        authenticate("target", PASSWORD, users, client_ip="198.51.100.250")
    assert excinfo.value.status_code == 429
    _login_limiter.requests.clear()


def test_the_limit_is_checked_before_the_expensive_verification():
    """Otherwise the limiter would not protect the thing it exists to protect."""
    from fastapi import HTTPException

    from app.auth import _login_limiter

    _login_limiter.requests.clear()
    ip = "192.0.2.10"
    for _ in range(_login_limiter.max_requests):
        authenticate("nobody", "x", {}, client_ip=ip)

    with pytest.raises(HTTPException):
        # No such user, so a verification would never run -- reaching 429 here
        # proves the check happens before the lookup rather than after it.
        authenticate("nobody", "x", {}, client_ip=ip)
    _login_limiter.requests.clear()


def test_authenticate_without_an_address_is_not_limited():
    """Internal callers that have no request context still work."""
    users = {"u": user_with(_hash_password(PASSWORD))}
    for _ in range(_login_limiter_max() + 5):
        assert authenticate("u", PASSWORD, users) is users["u"]


def _login_limiter_max() -> int:
    from app.auth import _login_limiter

    return _login_limiter.max_requests
