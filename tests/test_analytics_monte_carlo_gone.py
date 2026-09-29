"""Tests for /analytics/monte-carlo returning 410 Gone (Finding 6 fix)."""
import pytest


def test_analytics_monte_carlo_returns_410_gone(client):
    """POST /analytics/monte-carlo returns 410 Gone after the fix.

    The endpoint used an old ESG formula that overestimated scores by ~16 points
    on average and didn't see country data. It's been removed in favor of the
    correct endpoint at /api/v1/evaluate/monte-carlo.
    """
    response = client.post("/api/v1/analytics/monte-carlo", json={
        "name": "Test Project",
        "budget": 100000,
        "co2_reduction": 50,
        "social_impact": 7,
        "duration_months": 24,
        "region": "Germany",
        "simulations": 100,
    })

    assert response.status_code == 410
    data = response.json()
    assert "detail" in data

    # The detail should be a dict with structured information
    detail = data["detail"]
    assert isinstance(detail, dict)
    assert detail["replacement"] == "/api/v1/evaluate/monte-carlo"
    assert "removed" in detail["error"].lower() or "gone" in detail["error"].lower()


def test_analytics_monte_carlo_gone_points_to_correct_replacement(client):
    """The 410 Gone response identifies the correct replacement endpoint."""
    response = client.post("/api/v1/analytics/monte-carlo", json={
        "budget": 50000,
        "co2_reduction": 100,
        "social_impact": 8,
        "duration_months": 12,
        "region": "Sweden",
        "simulations": 50,
    })

    assert response.status_code == 410
    detail = response.json()["detail"]
    assert detail["replacement"] == "/api/v1/evaluate/monte-carlo"
    assert "formula" in detail["reason"].lower() or "incorrect" in detail["reason"].lower()


def test_the_replacement_endpoint_works(client):
    """The replacement /evaluate/monte-carlo works correctly.

    This is a control test: the replacement endpoint should still work.
    """
    response = client.post("/api/v1/evaluate/monte-carlo", json={
        "project_name": "Test Project",
        "region": "Germany",
        "budget_usd": 100000,
        "co2_reduction_tons_per_year": 50,
        "social_impact_score": 7,
        "project_duration_months": 24,
        "n": 50,
    })

    assert response.status_code == 200
    data = response.json()
    # The correct endpoint returns a histogram with mean, max, etc.
    assert "histogram" in data
    assert "mean" in data
    assert "max" in data
    assert data["failed"] >= 0  # Some simulations may fail, that's expected
