"""The JSON metrics endpoints stop publishing a per-endpoint breakdown.

Second line behind the keying fix in app/middleware.py. `requests_by_endpoint`
no longer holds identifiers, but "how often was each endpoint called" is
operational detail either way, and a future key could carry something again.

Not behind an authentication dependency: the admin dashboard reads both routes
with a plain `fetch` and uses four aggregate numbers. Requiring a token would
take a working page away to protect data that page never asked for. So the
aggregates stay public and the breakdown does not.
"""
import copy

import pytest

from app.middleware import METRICS

PUBLIC = ("requests_total", "errors_total", "avg_response_time_ms",
          "uptime_seconds", "predictions_total")
WITHHELD = ("requests_by_endpoint", "requests_by_status")


@pytest.fixture(autouse=True)
def a_populated_map():
    """`METRICS` is a process-global. Restored whole, not key by key.

    Removing only the injected endpoint left `requests_by_status["200"]`
    behind, and every request these tests make moves the totals and timings
    besides.
    """
    saved = copy.deepcopy(METRICS)
    METRICS["requests_by_endpoint"]["/api/v1/evaluations/{evaluation_id}"] = 3
    METRICS["requests_by_status"]["200"] = 3
    yield
    METRICS.clear()
    METRICS.update(saved)


@pytest.mark.parametrize("path", ["/api/v1/metrics", "/api/v1/system/metrics"])
def test_an_anonymous_caller_gets_no_breakdown(client, path):
    body = client.get(path).json()

    for key in WITHHELD:
        assert key not in body, key


@pytest.mark.parametrize("path", ["/api/v1/metrics", "/api/v1/system/metrics"])
def test_the_aggregates_stay_public(client, path):
    """The dashboard's four numbers. Withholding these would break a page to
    protect data it never reads."""
    body = client.get(path).json()

    for key in PUBLIC:
        assert key in body, key


def test_the_omission_is_stated_not_silent(client):
    """A caller receiving no breakdown and no note would reasonably conclude
    the map is empty -- a different and wrong fact about the deployment."""
    body = client.get("/api/v1/metrics").json()

    assert set(body["detail_withheld"]) == set(WITHHELD)


def test_an_admin_receives_the_breakdown(client):
    """Otherwise the withholding is satisfied by an endpoint that returns
    nothing to anyone, which is a different defect."""
    from app.auth import create_access_token

    token = create_access_token({"sub": "admin", "role": "admin"})
    body = client.get("/api/v1/metrics",
                      headers={"Authorization": f"Bearer {token}"}).json()

    assert "requests_by_endpoint" in body
    assert body["requests_by_endpoint"]["/api/v1/evaluations/{evaluation_id}"] == 3


def test_a_non_admin_token_gets_no_breakdown(client):
    from app.auth import create_access_token

    token = create_access_token({"sub": "viewer", "role": "viewer"})
    body = client.get("/api/v1/metrics",
                      headers={"Authorization": f"Bearer {token}"}).json()

    assert "requests_by_endpoint" not in body


def test_a_forged_token_gets_no_breakdown(client):
    body = client.get("/api/v1/metrics",
                      headers={"Authorization": "Bearer not.a.token"}).json()

    assert "requests_by_endpoint" not in body


def test_the_prometheus_surface_is_unchanged(client):
    """It emits fixed counters and never held a path histogram, so it was never
    the disclosure -- and must not become collateral damage of the fix."""
    response = client.get("/api/v1/metrics/prometheus")

    assert response.status_code == 200
    assert "evaluation_id" not in response.text
