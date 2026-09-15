"""Who may manage webhooks.

A test that only checks "401 without credentials" proves a gate exists and
nothing about whether it is the right gate: a dependency that rejects everyone,
including administrators, passes it just as well. That is what the first version
of this did, and it was green while telling me nothing.

The matrix is the point. Each row distinguishes a different way the gate could
be wrong.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.auth as auth
import app.api.webhooks as webhooks


@pytest.fixture
def client():
    """The database is stubbed so an authorised request reaches a real response
    rather than a connection error -- otherwise the admin row proves only that
    the request got past the gate, which is less than it should prove."""
    app = FastAPI()
    app.include_router(webhooks.router)

    class Row:
        id = 1
    class FakeSession:
        def get(self, *a, **kw):
            return Row()
        def delete(self, *a, **kw):
            pass
        def commit(self):
            pass
        def query(self, *a, **kw):
            return self
        def order_by(self, *a, **kw):
            return self
        def limit(self, *a, **kw):
            return self
        def all(self):
            return []

    app.dependency_overrides[webhooks.get_db] = lambda: FakeSession()
    return TestClient(app)


def token(sub, role):
    return {"Authorization": f"Bearer {auth.create_access_token({'sub': sub, 'role': role})}"}


ANONYMOUS = {}
GARBAGE = {"Authorization": "Bearer not-a-token"}
NO_SCHEME = {"Authorization": auth.create_access_token({"sub": "admin", "role": "admin"})}


@pytest.mark.parametrize("headers,expected,why", [
    (ANONYMOUS, 401, "no credentials at all"),
    (GARBAGE, 401, "a token that does not verify"),
    (NO_SCHEME, 401, "a valid token without the Bearer scheme"),
])
def test_unauthenticated_callers_are_refused(client, headers, expected, why):
    assert client.delete("/api/v1/webhooks/1", headers=headers).status_code == expected, why
    assert client.get("/api/v1/webhooks/deliveries", headers=headers).status_code == expected, why


def test_a_token_for_a_user_who_no_longer_exists_is_refused(client):
    """A signature stays valid after the account is gone; the subject lookup is
    what makes that not enough."""
    assert client.delete("/api/v1/webhooks/1",
                         headers=token("deleted-account", "admin")).status_code == 401


@pytest.mark.parametrize("role", ["viewer", "analyst"])
def test_an_authenticated_non_admin_is_refused_with_403(client, role):
    """403, not 401, and the distinction carries the weight.

    401 would mean "we do not know who you are". 403 means "we do, and it is not
    enough" -- which is the only response that shows the gate reads the role
    rather than merely the presence of a token. A dependency that denied
    everyone would answer 401 here and pass a test that only looked for refusal.
    """
    assert client.delete("/api/v1/webhooks/1", headers=token(role, role)).status_code == 403
    assert client.get("/api/v1/webhooks/deliveries", headers=token(role, role)).status_code == 403


def test_an_admin_is_admitted(client):
    """The row that stops this from being a blanket denial."""
    assert client.delete("/api/v1/webhooks/1", headers=token("admin", "admin")).status_code == 200
    assert client.get("/api/v1/webhooks/deliveries", headers=token("admin", "admin")).status_code == 200


def test_an_admin_still_cannot_register_an_internal_destination(client):
    """Authorisation and destination policy are separate questions, and passing
    the first does not answer the second."""
    r = client.post("/api/v1/webhooks",
                    headers=token("admin", "admin"),
                    json={"url": "http://127.0.0.1:9090/hook"})
    assert r.status_code == 400
    assert "refused" in r.text


def test_the_role_comes_from_the_store_and_not_from_the_token(client):
    """A token minted while the account was an administrator must not keep
    administering after the account is demoted.

    require_auth looks the subject up and builds UserInfo from `user["role"]`,
    ignoring the `role` claim it was handed. That is the correct choice and it is
    invisible: someone removing the lookup to "save a query" would read the claim
    instead, and every issued token would carry its privileges until expiry.

    The window would be ACCESS_TOKEN_EXPIRE_MINUTES, which is 30 -- not the seven
    days of REFRESH_TOKEN_EXPIRE_DAYS, which is a different token and was what an
    earlier version of this docstring quoted. Half an hour of stale privileges is
    still a demotion that does not take effect, and small enough that nobody
    would notice it in testing.
    """
    claims_admin_but_is_not = {
        "Authorization": f"Bearer {auth.create_access_token({'sub': 'viewer', 'role': 'admin'})}"
    }
    assert client.delete("/api/v1/webhooks/1",
                         headers=claims_admin_but_is_not).status_code == 403


def test_only_the_database_is_stubbed(client):
    """The gate under test must be the real one.

    Overriding the auth dependency instead of the database would make every row
    above pass regardless of what require_admin does, which is the same
    false-positive the matrix exists to avoid.
    """
    overridden = {getattr(k, "__name__", str(k)) for k in client.app.dependency_overrides}
    assert overridden == {"get_db"}, f"something other than the database is stubbed: {overridden}"
