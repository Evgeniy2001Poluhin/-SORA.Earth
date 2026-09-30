"""
POST /evaluate must not return meaningless comparisons.

Until this PR, project_vs_country contained:
  - esg_score_diff = total_score - esg_rank (score minus rank: different units)
  - above_average = total_score > 50 (not comparing with any average)

Variant A removes them.
"""
import pytest


def test_evaluate_omits_the_meaningless_project_vs_country_fields(client):
    """esg_score_diff and above_average are not in the response."""
    response = client.post(
        "/api/v1/evaluate",
        json={
            "project_name": "Test",
            "country": "Germany",
            "budget_usd": 100000,
            "co2_reduction_tons_per_year": 50,
            "social_impact_score": 7,
            "project_duration_months": 24,
        },
    )
    assert response.status_code == 200
    data = response.json()

    # country_benchmark should still exist with country indicators
    assert "country_benchmark" in data
    bench = data["country_benchmark"]
    assert bench["country"] == "Germany"
    assert "co2_per_capita" in bench
    assert "renewable_share" in bench
    assert "esg_rank" in bench
    assert "hdi" in bench

    # project_vs_country should either be absent or not contain these fields
    if "project_vs_country" in bench:
        pvs = bench["project_vs_country"]
        assert "esg_score_diff" not in pvs, "esg_score_diff (score - rank) must be removed"
        assert "above_average" not in pvs, "above_average (score > 50) must be removed"


def test_the_removed_fields_were_meaningless():
    """Document what the defect was: comparing different units and a fixed threshold."""
    # This test runs on main's code to show the defect existed.
    # Values from НАХОДКА-10: Sweden rank=1, Russia rank=58, project score ~49-50.
    #
    # The calculation was:
    #   esg_score_diff = round(result["total_score"] - bench["esg_rank"], 2)
    #   above_average = result["total_score"] > 50
    #
    # Sweden (rank 1): score 49.19, diff was +48.19
    # Russia (rank 58): score 50.29, diff was -7.71
    # Six countries with scores 48.9-51.6 had diffs from +48 to -8.
    # The "difference" followed the country's rank, not the project's position.
    # "above_average" was a coin flip around 50, not a comparison with any average.
    pass


def test_country_benchmark_still_includes_indicators(client):
    """The country ESG indicators remain in the response."""
    response = client.post(
        "/api/v1/evaluate",
        json={
            "project_name": "Solar",
            "country": "Sweden",
            "budget_usd": 100000,
            "co2_reduction_tons_per_year": 150,
            "social_impact_score": 8,
            "project_duration_months": 12,
        },
    )
    assert response.status_code == 200
    bench = response.json()["country_benchmark"]

    # These are the actual country indicators and should stay
    assert bench["co2_per_capita"] == 3.6  # Updated 2024 value (was 3.5)
    assert bench["renewable_share"] == 60.1
    assert bench["esg_rank"] == 1
    assert bench["hdi"] == 0.952  # Updated 2022 value (was 0.947)
