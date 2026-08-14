"""User accounts live for one process, and the API no longer says otherwise.

#169. `POST /auth/register` answered 200 and lost the account on the next
restart: `USERS_DB` is a module-level dict rebuilt at import from
`SORA_DEFAULT_*_PASSWORD`, there is no `users` table, and nothing loads a hash
from anywhere else.

That is not a bug in the route. It is the route promising something the
platform does not have, and a caveat in a docstring would not have changed what
an admin saw -- a success, and an account that was gone after the next deploy.

These tests pin both halves: the endpoint is absent, and the reason it had to
go is still true.
"""
import pytest


def test_the_register_endpoint_is_gone(client):
    """404, not 401 or 405.

    Checked as a status rather than by grepping the source: a route can be
    removed from one module and registered from another, and the question is
    what the API answers.
    """
    response = client.post("/api/v1/auth/register",
                           json={"username": "x", "password": "y", "role": "viewer"})

    assert response.status_code == 404, response.status_code


def test_it_is_absent_from_the_published_contract(client):
    """An endpoint in the OpenAPI document is a promise, whether or not it
    routes. A client generated from this file must not offer registration."""
    from app.main import app

    assert "/api/v1/auth/register" not in app.openapi()["paths"]


def test_there_is_no_users_table(client):
    """The condition that made the endpoint dishonest.

    If a `users` table ever appears, registration can come back -- and this
    test failing is how that gets noticed, rather than the endpoint being
    restored by someone who assumed it had been dropped by accident.
    """
    from app.database import Base

    assert "users" not in Base.metadata.tables, (
        "a users table exists; #169 was decided on the premise that it does not"
    )


def test_every_stored_hash_is_written_through_argon2(client):
    """Why `legacy_hash_count` is structurally zero.

    Not a false negative in a test: there is no write path that could put a
    legacy hash in the store, so the count is a property of the architecture.
    Stated here because #25's removal plan has nothing else to measure.
    """
    from app.auth import ARGON2_PREFIX, USERS_DB, legacy_hash_count

    assert USERS_DB, "the seeded accounts are missing"
    for name, user in USERS_DB.items():
        assert user["hashed_password"].startswith(ARGON2_PREFIX), name

    assert legacy_hash_count(USERS_DB) == 0


def test_the_seeded_roles_are_the_only_accounts(client):
    """Three, from the environment. The number the platform actually has."""
    from app.auth import USERS_DB

    assert set(USERS_DB) == {"admin", "analyst", "viewer"}
