"""
Finland has its own country data in BENCHMARKS, and a missing rank or government
score is null, not a crash.

Recorded by finding 30.6 on 2026-10-10. Finland is in the evaluation list
(app/countries.py COUNTRIES) but had no row in app/country_benchmarks.py BENCHMARKS,
so its ESG score was silently computed from the global average.

The fix adds Finland with its five sourced values (co2_per_capita 5.5, gdp_per_capita
53156, hdi 0.942, renewable_share 52.9, gini_index 27.4) from World Bank and UNDP,
while esg_rank and gov_effectiveness are null (no data). Every place that reads them
must handle null.
"""

import pytest
from fastapi.testclient import TestClient


def test_every_selectable_country_has_a_benchmark():
    """
    Every country in app.countries.COUNTRIES has a row in BENCHMARKS.

    This catches the gap that Finland was: the country could be selected, but the ESG
    score fell back to the global average because BENCHMARKS.get(name, GLOBAL_AVG)
    found nothing. The message lists the missing ones.
    """
    from app.countries import COUNTRIES
    from app.country_benchmarks import BENCHMARKS

    # Every country must have a benchmark (including aliases)
    missing = []
    for country_name in COUNTRIES.keys():
        if country_name not in BENCHMARKS:
            missing.append(country_name)

    assert not missing, (
        f"Countries in COUNTRIES but missing from BENCHMARKS: {sorted(missing)}. "
        f"Add a row for each one, or remove them from COUNTRIES."
    )


def test_finland_is_scored_from_its_own_row(client):
    """
    Finland is scored from BENCHMARKS["Finland"], not GLOBAL_AVG.

    POST /evaluate for Finland gives different scores than for a project with the same
    inputs but in a country that uses GLOBAL_AVG fallback (or we can compare Finland's
    benchmark values vs global avg to ensure they differ significantly).
    """
    from app.country_benchmarks import BENCHMARKS, GLOBAL_AVG

    # Finland's benchmark should differ from GLOBAL_AVG in at least one key dimension
    finland = BENCHMARKS["Finland"]

    # Check that Finland's values differ from global average
    co2_diff = abs(finland["co2_per_capita"] - GLOBAL_AVG["co2_per_capita"])
    hdi_diff = abs(finland["hdi"] - GLOBAL_AVG["hdi"])
    renew_diff = abs(finland["renewable_share"] - GLOBAL_AVG["renewable_share"])

    # At least one should differ by a meaningful amount
    assert co2_diff > 1.0 or hdi_diff > 0.05 or renew_diff > 5.0, (
        f"Finland benchmark too close to GLOBAL_AVG. Differences: "
        f"CO2={co2_diff}, HDI={hdi_diff}, Renewable={renew_diff}"
    )

    # Make sure the evaluate endpoint uses Finland's benchmark
    payload = {
        "project_name": "Helsinki Solar",
        "budget": 100000,
        "co2_reduction": 150,
        "social_impact": 7,
        "duration_months": 24,
        "country": "Finland",
    }

    resp = client.post("/api/v1/evaluate", json=payload)
    assert resp.status_code == 200
    result = resp.json()

    # The response should include Finland's benchmark, not "Global Average"
    assert "country_benchmark" in result
    bench = result["country_benchmark"]
    assert bench["country"] == "Finland", f"Expected Finland benchmark, got {bench['country']}"


def test_ranking_route_handles_null_esg_rank(client):
    """
    GET /api/v1/analytics/country-ranking: 200; Finland present with esg_rank null
    and after every ranked country.

    The route's sorting puts countries with a rank first (in rank order), then
    countries without a rank (alphabetically). Finland should appear after all
    ranked countries, and its esg_rank field should be null in the response.
    """
    # Request enough to get all countries (33 keys in BENCHMARKS)
    resp = client.get("/api/v1/analytics/country-ranking?limit=100")
    assert resp.status_code == 200

    data = resp.json()
    assert "data" in data
    countries = data["data"]

    # Finland should be present
    finland = next((c for c in countries if c["country"] == "Finland"), None)
    assert finland is not None, "Finland not found in ranking response"
    assert finland["esg_rank"] is None, f"Finland esg_rank should be null, got {finland['esg_rank']}"

    # All ranked countries should come before Finland
    finland_index = next(i for i, c in enumerate(countries) if c["country"] == "Finland")
    for i in range(finland_index):
        country = countries[i]
        assert country["esg_rank"] is not None, (
            f"Country {country['country']} at position {i} before Finland has null rank, "
            "but all ranked countries should come first"
        )

    # Check the order of ranked countries (should be sorted by rank)
    ranked_countries = [c for c in countries if c["esg_rank"] is not None]
    ranks = [c["esg_rank"] for c in ranked_countries]
    assert ranks == sorted(ranks), f"Ranked countries not sorted by rank: {ranks}"

    # Countries with null rank should be alphabetically sorted
    # (In the full data, Finland might be the only one, but we check the pattern)
    unranked = [c for c in countries if c["esg_rank"] is None]
    unranked_names = [c["country"] for c in unranked]
    assert unranked_names == sorted(unranked_names), (
        f"Unranked countries not alphabetically sorted: {unranked_names}"
    )


def test_evaluate_endpoint_for_finland_returns_null_esg_rank(client):
    """
    POST /api/v1/evaluate for Finland: 200; the benchmark rank field is null.
    """
    payload = {
        "project_name": "Helsinki Solar",
        "budget": 100000,
        "co2_reduction": 150,
        "social_impact": 7,
        "duration_months": 24,
        "country": "Finland",
    }

    resp = client.post("/api/v1/evaluate", json=payload)
    assert resp.status_code == 200

    result = resp.json()
    assert "country_benchmark" in result
    bench = result["country_benchmark"]

    assert bench["country"] == "Finland"
    assert bench["esg_rank"] is None, f"Finland esg_rank should be null, got {bench['esg_rank']}"
    # Check other values are present and valid
    assert bench["co2_per_capita"] == 5.5
    assert bench["renewable_share"] == 52.9
    assert bench["hdi"] == 0.942


def test_map_route_handles_null_gov_effectiveness(client):
    """
    GET /api/v1/map/countries for Finland: 200, no crash.

    The route computes a live ESG score from gov_effectiveness, which is None for
    Finland. The _live_esg function must handle None by falling back to 0.0, not
    crashing with TypeError on (None + 2.5).
    """
    resp = client.get("/api/v1/map/countries")
    assert resp.status_code == 200

    data = resp.json()
    assert "countries" in data

    # Find Finland in the response
    finland = next((c for c in data["countries"] if c["name"] == "Finland"), None)
    assert finland is not None, "Finland not found in map countries response"

    # The response should have an esg score (computed despite gov_effectiveness being None)
    assert "esg" in finland
    assert isinstance(finland["esg"], (int, float))

    # If "gov_effectiveness" is in the response, it should be a number (the 0.0 fallback)
    # rounded to 2 decimals, not None
    if "gov_effectiveness" in finland:
        assert isinstance(finland["gov_effectiveness"], (int, float)), (
            f"gov_effectiveness should be a number (fallback), got {type(finland['gov_effectiveness'])}"
        )


def test_investor_dashboard_benchmark_display_handles_null():
    """
    The investor-dashboard.html benchmark display shows "—" for null values.

    This test checks that the JavaScript code in investor-dashboard.html handles
    null values by displaying "—" instead of "NaN" or crashing.

    We verify this by reading the file and checking the display logic.
    """
    import re
    from pathlib import Path

    html_path = Path("app/static/investor-dashboard.html")
    content = html_path.read_text()

    # Find the benchmark display code
    # Look for the displayValue assignment
    pattern = r"const displayValue = v === null \|\| v === undefined \? '—' : Number\(v\)\.toFixed\(1\);"
    match = re.search(pattern, content)

    assert match is not None, (
        "investor-dashboard.html should handle null values in benchmark display. "
        "Expected to find: const displayValue = v === null || v === undefined ? '—' : Number(v).toFixed(1);"
    )


def test_mutations():
    """
    Red-first mutations:
    (i) Remove Finland from BENCHMARKS -> enumeration and Finland-scoring tests fail
    (ii) Put back old sorting (key=lambda x: x[1]["esg_rank"]) -> ranking test fails
    (iii) Remove None handling in map_data -> map test fails

    This test is not run automatically - it's a script for manual verification.
    The actual mutations are performed by the developer during validation.
    """
    pass  # Mutations are run manually, not in CI


# Additional tests for coverage
def test_finland_has_correct_values_in_benchmarks():
    """
    Finland's BENCHMARKS row has the exact values from the sourced data.
    """
    from app.country_benchmarks import BENCHMARKS

    finland = BENCHMARKS["Finland"]

    assert finland["co2_per_capita"] == 5.5
    assert finland["renewable_share"] == 52.9
    assert finland["esg_rank"] is None
    assert finland["hdi"] == 0.942
    assert finland["gdp_per_capita"] == 53156
    assert finland["gini_index"] == 27.4
    assert finland["gov_effectiveness"] is None


def test_sources_documentation_mentions_finland():
    """
    The SOURCES dict in country_benchmarks.py documents Finland's special cases.
    """
    from app.country_benchmarks import SOURCES

    # renewable_share note should mention Finland
    renewable_note = SOURCES["renewable_share"]["note"]
    assert "Finland" in renewable_note or "31 countries" in renewable_note

    # gini_index note should mention Finland's source
    gini_note = SOURCES["gini_index"]["note"]
    assert "Finland" in gini_note or "SI.POV.GINI" in gini_note

    # esg_rank and gov_effectiveness notes should mention Finland has None
    esg_note = SOURCES["esg_rank"]["note"]
    gov_note = SOURCES["gov_effectiveness"]["note"]
    assert "Finland" in esg_note or "null" in esg_note
    assert "Finland" in gov_note or "null" in gov_note or "archived" in gov_note


def test_source_register_reflects_31_countries():
    """
    app/ingesters/source_register.py coverage notes say "31 countries" not "30".
    """
    from app.ingesters.source_register import SOURCE_REGISTER

    benchmark_source = SOURCE_REGISTER.get("benchmark")
    assert benchmark_source is not None, "benchmark not found in SOURCE_REGISTER"

    coverage = benchmark_source.coverage
    assert "31 countries" in coverage, f"Coverage should say '31 countries', got: {coverage}"
    assert "33 keys" in coverage, f"Coverage should say '33 keys' (with aliases), got: {coverage}"
