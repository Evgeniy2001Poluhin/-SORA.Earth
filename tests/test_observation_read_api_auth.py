"""The observation reader is admin-only, and the refusal is the tested half.

Same reasoning as tests/test_ingestion_attention_auth.py: the view's own tests
override the dependency, so without this file the auth would be asserted
nowhere and could be deleted without a single failure.

What it protects is narrower than `attention` but real. The rows carry
coordinates, source names and the exact times a system reads external APIs --
operator surface, on an endpoint sitting beside public ones.
"""


def test_it_refuses_an_unauthenticated_caller(client):
    response = client.get("/api/v1/observations")

    assert response.status_code in (401, 403), response.status_code


def test_it_refuses_a_non_admin_token(client):
    from fastapi import HTTPException

    from app.auth import require_admin
    from app.main import app

    def _not_admin():
        raise HTTPException(status_code=403, detail="Admin access required")

    app.dependency_overrides[require_admin] = _not_admin
    try:
        response = client.get("/api/v1/observations")
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
        response = client.get("/api/v1/observations")
    finally:
        app.dependency_overrides.pop(require_admin, None)

    assert response.status_code == 200
    assert set(response.json()) == {
        "count", "limit", "offset", "has_more", "filters", "observations"}


def test_the_response_shape_is_declared_not_just_returned(client):
    """`response_model`, so the contract is in the OpenAPI document.

    #84 was found by asking whether the API exposed `measurement_kind` and
    being unable to answer -- there was no API. An endpoint whose shape is not
    declared leaves the same question unanswerable one layer further in.
    """
    from app.main import app

    schema = app.openapi()
    operation = schema["paths"]["/api/v1/observations"]["get"]
    ref = operation["responses"]["200"]["content"]["application/json"]["schema"]

    assert ref.get("$ref", "").endswith("ObservationPage"), ref

    model = schema["components"]["schemas"]["ObservationRow"]
    for field in ("source", "measurement_kind", "model", "indicator", "unit",
                  "event_time", "ingested_at", "temporal_kind",
                  "latitude", "longitude", "region_id"):
        assert field in model["properties"], field

    # The one field that must not be there.
    assert "metadata_json" not in model["properties"]
