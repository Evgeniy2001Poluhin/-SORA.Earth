"""Password storage: Argon2id only, persisted in the `users` table.

Two SHA-256 formats used to be accepted and upgraded on login. No store ever
held one -- accounts were rebuilt from the environment on every import, hashed
with Argon2id -- so the fallback was a way in with nothing behind it, and it is
gone. These tests pin that a SHA-256 value no longer verifies, that a login
re-hashes an Argon2id hash made at weaker parameters and stores the result in
the database, and that the re-hash can never overwrite a reset that committed
while the old password was being checked.
"""
import hashlib

import pytest
import sqlalchemy as sa
from argon2 import PasswordHasher

from app import accounts
from app.auth import (
    ARGON2_FLOOR,
    ARGON2_PREFIX,
    _hash_password,
    authenticate,
    needs_rehash,
    verify_password,
)
from app.database import SessionLocal, User

PASSWORD = "correct horse battery staple"


def legacy_salted(password: str, salt: str = "a" * 32) -> str:
    """The format _hash_password produced before Argon2id."""
    return f"{salt}${hashlib.sha256(f'{salt}{password}'.encode()).hexdigest()}"


def legacy_unsalted(password: str) -> str:
    """The format before that."""
    return hashlib.sha256(password.encode()).hexdigest()


def weak_argon2(password: str) -> str:
    """Argon2id, but at the floor rather than the current parameters."""
    return PasswordHasher(**ARGON2_FLOOR).hash(password)


def stored_hash(user_id) -> str:
    with SessionLocal() as session:
        return session.get(User, user_id).password_hash


def set_hash(user_id, value: str) -> None:
    with SessionLocal() as session:
        session.execute(sa.update(User).where(User.id == user_id).values(password_hash=value))
        session.commit()


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


# ------------------------------------------------- SHA-256 is no longer a way in


@pytest.mark.parametrize("hashed", [legacy_salted(PASSWORD), legacy_unsalted(PASSWORD)])
def test_a_sha256_value_does_not_verify_even_with_the_right_password(hashed):
    assert verify_password(PASSWORD, hashed) is False


@pytest.mark.parametrize("hashed", [legacy_salted(PASSWORD), legacy_unsalted(PASSWORD)])
def test_the_table_refuses_to_store_a_sha256_value(make_account, hashed):
    """The CHECK constraint, not only the verifier: nothing can put one back."""
    user = make_account()
    with pytest.raises(sa.exc.IntegrityError):
        set_hash(user.id, hashed)


@pytest.mark.parametrize("garbage", ["", "not-a-hash", "$argon2id$broken", "$", "a$b$c"])
def test_malformed_hashes_return_false_rather_than_raising(garbage):
    """A corrupt stored hash must fail the login, not 500 the endpoint."""
    assert verify_password(PASSWORD, garbage) is False


# ----------------------------------------------------------- login and re-hash


def test_authenticate_returns_the_current_account(make_account):
    user = make_account()
    account = authenticate(user.username, PASSWORD)
    assert account is not None
    assert account.id == user.id and account.token_version == user.token_version


def test_authenticate_rehashes_a_weak_hash_and_stores_it(make_account):
    user = make_account()
    weak = weak_argon2(PASSWORD)
    set_hash(user.id, weak)

    account = authenticate(user.username, PASSWORD)

    assert account is not None
    stored = stored_hash(user.id)
    assert stored != weak, "the weaker hash was not replaced in the database"
    assert stored.startswith("$argon2id$") and not needs_rehash(stored)
    assert verify_password(PASSWORD, stored)
    # The same password, so the same session generation: a re-hash must not
    # log the account out of every device.
    assert account.token_version == user.token_version


def test_authenticate_leaves_a_current_hash_alone(make_account):
    user = make_account()
    before = stored_hash(user.id)
    authenticate(user.username, PASSWORD)
    assert stored_hash(user.id) == before


def test_a_failed_login_does_not_rehash_anything(make_account):
    """The plaintext is only known-good after verification."""
    user = make_account()
    weak = weak_argon2(PASSWORD)
    set_hash(user.id, weak)

    assert authenticate(user.username, "wrong") is None
    assert stored_hash(user.id) == weak


def test_a_rehash_never_overwrites_a_reset_that_committed_meanwhile(make_account):
    """The re-hash is conditional on the hash and version the login verified.

    Simulated directly: the login read `state`, a reset committed, and only
    then does the re-hash try to land. It must find nothing to update.
    """
    user = make_account()
    with SessionLocal() as session:
        state = accounts.state_by_id(session, user.id)
        accounts.reset_password(session, user.id, "another long password here",
                                hash_password=_hash_password)
        after_reset = accounts.state_by_id(session, user.id).password_hash
        assert accounts.store_rehash(session, state, _hash_password(PASSWORD)) is False

    assert stored_hash(user.id) == after_reset
    assert verify_password("another long password here", stored_hash(user.id))


def test_authenticate_rejects_an_unknown_user():
    assert authenticate("nobody-at-all", PASSWORD) is None


def test_authenticate_rejects_a_blocked_account(make_account):
    user = make_account(is_blocked=True)
    assert authenticate(user.username, PASSWORD) is None


def test_every_stored_account_is_argon2id():
    with SessionLocal() as session:
        hashes = list(session.execute(sa.select(User.password_hash)).scalars())
    assert hashes, "the seeded accounts are missing"
    assert all(h.startswith("$argon2id$") for h in hashes)


# ------------------------------------------------------------------ end to end


def test_login_over_http_rehashes_a_weak_hash(client, make_account):
    """The whole point, exercised through the endpoint rather than the helper."""
    user = make_account()
    weak = weak_argon2(PASSWORD)
    set_hash(user.id, weak)

    response = client.post("/api/v1/auth/login-json",
                           json={"username": user.username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    assert response.json()["access_token"]

    stored = stored_hash(user.id)
    assert stored != weak and stored.startswith(ARGON2_PREFIX)
    assert verify_password(PASSWORD, stored)


# ------------------------------------------------- cost floor and DoS bounding


def test_the_cost_floor_cannot_be_configured_away(monkeypatch):
    """Raising the parameters is expected; lowering them past OWASP's floor is not.

    _argon2_params() is exercised directly rather than by reloading the module.
    A reload rebinds the limiter and the hasher, while the route
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


def test_login_is_rate_limited_per_source_address(make_account):
    """Argon2 makes login expensive by design, so it has to be bounded.

    Without this, an unauthenticated caller can spend ~130 ms of CPU and 64 MiB
    per request with no ceiling.
    """
    from fastapi import HTTPException

    from app.auth import _login_limiter

    user = make_account()
    ip = "203.0.113.77"
    _login_limiter.requests.clear()

    for _ in range(_login_limiter.max_requests):
        authenticate(user.username, "wrong", client_ip=ip)

    with pytest.raises(HTTPException) as excinfo:
        authenticate(user.username, PASSWORD, client_ip=ip)
    assert excinfo.value.status_code == 429
    _login_limiter.requests.clear()


def test_the_budget_is_spent_per_account_as_well_as_per_address(make_account):
    """A distributed attempt on one account must also be bounded."""
    from fastapi import HTTPException

    from app.auth import _login_limiter

    user = make_account()
    _login_limiter.requests.clear()

    for i in range(_login_limiter.max_requests):
        authenticate(user.username, "wrong", client_ip=f"198.51.100.{i}")

    with pytest.raises(HTTPException) as excinfo:
        authenticate(user.username, PASSWORD, client_ip="198.51.100.250")
    assert excinfo.value.status_code == 429
    _login_limiter.requests.clear()


def test_the_limit_is_checked_before_the_expensive_verification():
    """Otherwise the limiter would not protect the thing it exists to protect."""
    from fastapi import HTTPException

    from app.auth import _login_limiter

    _login_limiter.requests.clear()
    ip = "192.0.2.10"
    for _ in range(_login_limiter.max_requests):
        authenticate("nobody", "x", client_ip=ip)

    with pytest.raises(HTTPException):
        # No such user, so a verification would never run -- reaching 429 here
        # proves the check happens before the lookup rather than after it.
        authenticate("nobody", "x", client_ip=ip)
    _login_limiter.requests.clear()


def test_authenticate_without_an_address_is_not_limited(make_account):
    """Internal callers that have no request context still work."""
    user = make_account()
    for _ in range(_login_limiter_max() + 5):
        assert authenticate(user.username, PASSWORD).id == user.id


def _login_limiter_max() -> int:
    from app.auth import _login_limiter

    return _login_limiter.max_requests
