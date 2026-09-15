"""The database writers refuse an anonymous caller (Security D, GHSA-g23j-9c49-xpjg).

`DELETE /api/v1/history` executed `db.query(Evaluation).delete()` — an
unconditional mass delete of the evaluation table — with no authorization, no
parameters, no confirmation. `DELETE /api/v1/history/{eval_id}` and
`POST /api/v1/infra/data-refresh/run` were open the same way. They now require
`require_admin`.

The mass-delete case is exercised behaviourally with the database session
replaced by a mock, so this test can never delete anything even when run against
the unguarded code it is red on: the assertion is that the handler is refused
*before* it runs, which the mock proves by never being touched.
"""
from unittest.mock import MagicMock

import pytest

from app.auth import create_access_token


def test_the_mass_delete_is_refused_before_it_can_run(client, monkeypatch):
    import app.main as main

    session = MagicMock(name="db_session")
    monkeypatch.setattr(main, "get_db_sync", lambda: session, raising=False)

    anon = client.request("DELETE", "/api/v1/history")
    assert anon.status_code in (401, 403), (
        "DELETE /history answered %d to an anonymous caller" % anon.status_code
    )
    # The decisive assertion: the handler never reached the database. If it had,
    # this is the call that would have emptied the table.
    assert session.query.call_count == 0, "the mass-delete handler ran for an anonymous caller"

    viewer = create_access_token({"sub": "viewer"})
    forbidden = client.request("DELETE", "/api/v1/history",
                               headers={"Authorization": f"Bearer {viewer}"})
    assert forbidden.status_code == 403
    assert session.query.call_count == 0, "the mass-delete handler ran for a viewer"


def test_the_writer_routes_carry_the_admin_dependency(client):
    """Structural, and safe: none of these run. Covers the single-record delete
    and the refresh trigger alongside the mass delete."""
    from fastapi.routing import APIRoute

    AUTH = {"require_auth", "require_admin", "require_analyst_or_admin",
            "require_api_key", "require_admin_apikey", "admin_auth"}

    def auth_names(dependant, acc=None):
        acc = acc if acc is not None else []
        for sub in dependant.dependencies:
            name = getattr(sub.call, "__name__", type(sub.call).__name__)
            if name in AUTH:
                acc.append(name)
            auth_names(sub, acc)
        return acc

    want = {
        ("DELETE", "/api/v1/history"),
        ("DELETE", "/api/v1/history/{eval_id}"),
        ("POST", "/api/v1/infra/data-refresh/run"),
    }
    seen = {}
    for route in client.app.routes:
        if isinstance(route, APIRoute):
            for method in route.methods:
                if (method, route.path) in want:
                    seen[(method, route.path)] = "require_admin" in auth_names(route.dependant)

    missing = sorted(k for k in want if not seen.get(k))
    assert not missing, "writer routes without require_admin: %s" % missing
