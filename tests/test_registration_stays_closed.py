"""There is still no self-service registration.

`POST /auth/register` was removed because it answered 200 and lost the account
on the next restart: accounts lived in a module-level dict. They now live in
the `users` table and survive, and an administrator creates them through
`POST /api/v1/admin/users`. Registration stays closed anyway -- the pilot
issues accounts by invitation, and an open endpoint would hand anyone a
`specialist` account.

So what these tests pin changed: not "accounts do not persist", which is no
longer true, but "nobody can create one without an administrator".
"""
import pytest


def test_the_register_endpoint_answers_nothing_useful(client):
    """Absent, and checked as a status rather than by grepping the source: a
    route can be removed from one module and registered from another, and the
    question is what the API answers.

    The status is asserted as "not a success" rather than as 404. The first
    version of this said 404 and was green for a reason that had nothing to do
    with registration: whether an absent path answers 404 or 405 depended on
    whether the SPA had been built, because a second catch-all is registered
    only when it has. This suite never builds it; the image does. So the same
    commit answered 404 here and 405 on production.

    That is fixed, and it is pinned where it belongs -- in
    tests/test_absent_paths_are_not_wrong_method.py, which now registers the
    SPA route itself instead of waiting for CI to acquire one. What this test
    owns is that registration is gone, which is true under either answer.
    """
    response = client.post("/api/v1/auth/register",
                           json={"username": "x", "password": "y", "role": "viewer"})

    assert response.status_code in (404, 405), response.status_code
    assert not response.is_success

    # And nothing was created by the attempt.
    from app import accounts
    from app.database import SessionLocal
    with SessionLocal() as session:
        assert accounts.state_by_username(session, "x") is None


def test_it_is_absent_from_the_published_contract(client):
    """An endpoint in the OpenAPI document is a promise, whether or not it
    routes. A client generated from this file must not offer registration."""
    from app.main import app

    assert "/api/v1/auth/register" not in app.openapi()["paths"]


def test_accounts_are_created_only_by_an_administrator(client):
    """The one way an account comes into being, and who may use it."""
    body = {"username": "would-be-participant", "password": "a long enough password", "role": "specialist"}
    assert client.post("/api/v1/admin/users", json=body).status_code == 401

    from tests._auth_tokens import bearer
    for role in ("viewer", "analyst"):
        assert client.post("/api/v1/admin/users", json=body,
                           headers=bearer(role)).status_code == 403, role
