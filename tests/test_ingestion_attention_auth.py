"""The attention view is admin-only, and the refusal is the tested half.

`failure_reason` is `f"{type(raised).__name__}: {raised}"` -- the text of
whatever the run raised. For a database failure that is infrastructure detail:
host, user, driver, sometimes the reason authentication was refused. The other
routes in app/api/infra.py are public, which is not a reason to add one more
that leaks more than they do.

The view's own tests override the dependency, so without this file the auth
would be asserted nowhere and could be removed without a single failure.
"""
import pytest


def test_it_refuses_an_unauthenticated_caller(client):
    response = client.get("/api/v1/ingestion/attention")

    assert response.status_code in (401, 403), response.status_code


def test_it_refuses_a_non_admin_token(client):
    from app.auth import require_admin
    from app.main import app
    from fastapi import HTTPException

    def _not_admin():
        raise HTTPException(status_code=403, detail="Admin access required")

    app.dependency_overrides[require_admin] = _not_admin
    try:
        response = client.get("/api/v1/ingestion/attention")
    finally:
        app.dependency_overrides.pop(require_admin, None)

    assert response.status_code == 403


def test_an_admin_gets_through(client):
    """Otherwise the refusals are satisfied by an endpoint that refuses
    everyone, which is a different defect wearing the same status code."""
    from app.auth import require_admin
    from app.main import app

    app.dependency_overrides[require_admin] = lambda: {"username": "t", "role": "admin"}
    try:
        response = client.get("/api/v1/ingestion/attention")
    finally:
        app.dependency_overrides.pop(require_admin, None)

    assert response.status_code == 200
    assert set(response.json()) == {"count", "needs_attention", "sources"}


def test_the_response_shape_is_declared_not_just_returned(client):
    """`response_model` is what makes the contract visible and enforced.

    CLAUDE.md: "For a new API endpoint, define its Pydantic schema in
    app/schemas.py." Without it the endpoint returns whatever the dict happens
    to hold, the OpenAPI document says nothing, and a field can be dropped
    without a single failure -- which a mutation confirmed.
    """
    from app.main import app

    schema = app.openapi()
    operation = schema["paths"]["/api/v1/ingestion/attention"]["get"]
    ref = operation["responses"]["200"]["content"]["application/json"]["schema"]

    assert ref.get("$ref", "").endswith("IngestionAttention"), ref

    model = schema["components"]["schemas"]["IngestionAttentionRow"]
    for field in ("source", "required_action", "freshness_status",
                  "source_vintage_seconds", "max_vintage_seconds"):
        assert field in model["properties"], field
