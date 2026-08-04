"""Refresh-token revocation, against a real Redis where the point requires one.

The gap this closes was a process-local `set` of issued tokens: a restart emptied
it and logged everyone out, two processes did not share it, and it grew without
bound. So the tests that matter here are the ones a single in-process structure
cannot pass -- survival across a restart, and visibility from a second process.
Those are marked and skipped when no Redis is reachable, rather than quietly
passing against the fallback and proving nothing.
"""
import hashlib
import os
import time

import pytest

import app.auth as auth


@pytest.fixture(autouse=True)
def clean_local_state():
    auth._revoked_locally.clear()
    yield
    auth._revoked_locally.clear()


def _redis_or_skip():
    client = auth._redis()
    if client is None:
        pytest.skip("no Redis reachable; the cross-process properties cannot be shown")
    return client


# ------------------------------------------------------------ basic contract

def test_a_fresh_token_validates():
    token = auth.create_refresh_token({"sub": "alice", "role": "admin"})
    assert auth.validate_refresh_token(token)["sub"] == "alice"


def test_a_revoked_token_is_refused():
    token = auth.create_refresh_token({"sub": "alice", "role": "admin"})
    auth.revoke_refresh_token(token)
    with pytest.raises(ValueError, match="revoked"):
        auth.validate_refresh_token(token)


def test_revoking_one_token_leaves_the_others_alone():
    a = auth.create_refresh_token({"sub": "alice", "role": "admin"})
    b = auth.create_refresh_token({"sub": "bob", "role": "user"})
    auth.revoke_refresh_token(a)
    assert auth.validate_refresh_token(b)["sub"] == "bob"


def test_a_forged_signature_is_refused_without_consulting_the_store():
    token = auth.create_refresh_token({"sub": "alice", "role": "admin"})
    with pytest.raises(ValueError, match="signature"):
        auth.validate_refresh_token(token[:-4] + "AAAA")


def test_an_access_token_is_not_accepted_as_a_refresh_token():
    with pytest.raises(ValueError, match="Not a refresh token"):
        auth.validate_refresh_token(auth.create_access_token({"sub": "alice"}))


def test_an_expired_token_is_refused(monkeypatch):
    monkeypatch.setattr(auth, "REFRESH_TOKEN_EXPIRE_DAYS", -1)
    token = auth.create_refresh_token({"sub": "alice", "role": "admin"})
    with pytest.raises(ValueError, match="expired"):
        auth.validate_refresh_token(token)


# --------------------------------------------- what the old structure could not do

def test_a_token_survives_a_restart():
    """The defect, stated as a test.

    The previous store held issued tokens in memory, so an empty set after a
    restart meant every refresh token in the world was refused. Clearing the
    process-local state stands in for that restart.
    """
    token = auth.create_refresh_token({"sub": "alice", "role": "admin"})
    auth._revoked_locally.clear()
    assert auth.validate_refresh_token(token)["sub"] == "alice"


def test_a_revocation_survives_a_restart():
    """And the converse, which is the part that must not regress."""
    _redis_or_skip()
    token = auth.create_refresh_token({"sub": "alice", "role": "admin"})
    auth.revoke_refresh_token(token)

    auth._revoked_locally.clear()   # the process forgets

    with pytest.raises(ValueError, match="revoked"):
        auth.validate_refresh_token(token)


def test_a_revocation_is_visible_to_another_process():
    """`backend` revokes; `scheduler` must refuse. Each had its own set before."""
    client = _redis_or_skip()
    token = auth.create_refresh_token({"sub": "alice", "role": "admin"})
    auth.revoke_refresh_token(token)

    # Reach past this process entirely: ask Redis what a second process would see.
    fingerprint = hashlib.sha256(token.encode()).hexdigest()
    assert client.exists(auth._REVOKED_PREFIX + fingerprint)


def test_the_store_holds_a_fingerprint_and_not_the_token():
    """A revocation list holding live credentials is a credential store nobody
    decided to build."""
    client = _redis_or_skip()
    token = auth.create_refresh_token({"sub": "alice", "role": "admin"})
    auth.revoke_refresh_token(token)

    keys = [k for k in client.scan_iter(auth._REVOKED_PREFIX + "*")]
    assert keys, "nothing was recorded"
    for key in keys:
        assert token not in key


def test_the_entry_expires_with_the_token_and_no_later():
    """An entry that outlived the token would accumulate for ever; one that died
    early would make a revoked token valid again."""
    client = _redis_or_skip()
    token = auth.create_refresh_token({"sub": "alice", "role": "admin"})
    auth.revoke_refresh_token(token)

    fingerprint = hashlib.sha256(token.encode()).hexdigest()
    ttl = client.ttl(auth._REVOKED_PREFIX + fingerprint)
    lifetime = auth.REFRESH_TOKEN_EXPIRE_DAYS * 86400
    assert 0 < ttl <= lifetime, f"ttl={ttl}, token lifetime={lifetime}"
    assert ttl > lifetime - 60, "the entry dies well before the token does"


def test_an_already_expired_token_is_not_recorded(monkeypatch):
    client = _redis_or_skip()
    monkeypatch.setattr(auth, "REFRESH_TOKEN_EXPIRE_DAYS", -1)
    token = auth.create_refresh_token({"sub": "alice", "role": "admin"})
    before = len(list(client.scan_iter(auth._REVOKED_PREFIX + "*")))

    auth.revoke_refresh_token(token)

    after = len(list(client.scan_iter(auth._REVOKED_PREFIX + "*")))
    assert after == before, "an expired token was written to the revocation list"


def test_revocation_still_holds_locally_when_redis_is_gone(monkeypatch):
    """Refusing every refresh during a cache outage would turn it into a
    site-wide logout, so the fallback degrades rather than fails closed."""
    monkeypatch.setattr(auth, "_redis", lambda: None)
    token = auth.create_refresh_token({"sub": "alice", "role": "admin"})
    auth.revoke_refresh_token(token)
    with pytest.raises(ValueError, match="revoked"):
        auth.validate_refresh_token(token)


def test_two_logins_in_the_same_second_are_different_tokens():
    """Found by a test that failed for the right reason.

    The payload was (sub, role, type, iat, exp) at one-second resolution, so two
    logins by the same account in the same second produced byte-identical tokens.
    Revoking either revoked both -- logging out of one device logged out the
    other. The defect predates the revocation list: the old allowlist removed the
    same shared string.
    """
    first = auth.create_refresh_token({"sub": "alice", "role": "admin"})
    second = auth.create_refresh_token({"sub": "alice", "role": "admin"})
    assert first != second

    auth.revoke_refresh_token(first)
    assert auth.validate_refresh_token(second)["sub"] == "alice", \
        "revoking one session refused another"
