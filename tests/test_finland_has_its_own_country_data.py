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




def test_finland_is_scored_from_its_own_row(client):
    """
    Finland is scored from BENCHMARKS["Finland"], not GLOBAL_AVG.

    calculate_esg looks up BENCHMARKS.get(project.region, GLOBAL_AVG) and uses it to
    compute the three sub-scores. This test calls /evaluate for Finland and verifies
    the environment_score, social_score, and economic_score match what _esg_components
    produces with BENCHMARKS["Finland"], and differ from what it produces with GLOBAL_AVG.
    """
    from app.main import _esg_components, REGIONAL_FACTORS
    from app.country_benchmarks import BENCHMARKS, GLOBAL_AVG

    # Finland is in region "Europe" (app/countries.py), so evaluate uses
    # calculate_esg(project, "Europe"), which uses REGIONAL_FACTORS["Europe"]
    rf = REGIONAL_FACTORS["Europe"]

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

    # Compute expected scores from Finland's benchmark
    finland_env, finland_soc, finland_eco = _esg_components(
        payload["budget"],
        payload["co2_reduction"],
        payload["social_impact"],
        BENCHMARKS["Finland"],
        rf,
    )

    # The response sub-scores should match Finland's benchmark (scaled to 0-100, rounded to 1 decimal)
    assert result["environment_score"] == round(finland_env * 100, 1), (
        f"Environment score {result['environment_score']} != expected {round(finland_env * 100, 1)} from Finland benchmark"
    )
    assert result["social_score"] == round(finland_soc * 100, 1)
    assert result["economic_score"] == round(finland_eco * 100, 1)

    # Compute what the scores would be with GLOBAL_AVG
    global_env, global_soc, global_eco = _esg_components(
        payload["budget"],
        payload["co2_reduction"],
        payload["social_impact"],
        GLOBAL_AVG,
        rf,
    )

    # At least one sub-score must differ from GLOBAL_AVG by more than 0.5 points
    env_diff = abs(result["environment_score"] - round(global_env * 100, 1))
    soc_diff = abs(result["social_score"] - round(global_soc * 100, 1))
    eco_diff = abs(result["economic_score"] - round(global_eco * 100, 1))

    assert max(env_diff, soc_diff, eco_diff) > 0.5, (
        f"Finland scores too close to GLOBAL_AVG. "
        f"Env diff: {env_diff}, Soc diff: {soc_diff}, Eco diff: {eco_diff}. "
        f"This suggests calculate_esg is using GLOBAL_AVG instead of Finland's benchmark."
    )


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


def test_map_route_handles_null_gov_effectiveness(monkeypatch):
    """
    Finland with no gov_effectiveness in the pinned table: that input is missing, never a default 0.0.

    Under C8 part 2 the map scores from the pinned table only, not from BENCHMARKS, so
    this test builds a table in which Finland's governance row is absent. The score is
    null and incomplete, gov_effectiveness is named in missing_inputs, and Finland's
    Gini qualifies from its own sourced row.
    """
    import copy
    from datetime import date
    from app import map_inputs
    from app.api.map_data import score_country
    from tests.test_map_inputs import SHIPPED_DOC

    monkeypatch.setattr(map_inputs, "use_date", lambda: date(2026, 10, 19))
    doc = copy.deepcopy(SHIPPED_DOC)
    fin = next(c for c in doc["countries"] if c["iso3"] == "FIN")
    fin["inputs"]["gov_effectiveness"] = {"reason": "no_observation", "value": None}
    fin["complete"] = False
    fin["missing"] = ["gov_effectiveness"]
    rows = {c["iso3"]: c for c in doc["countries"]}
    table = map_inputs.PinnedTable(rows=rows, table_sha256="c" * 64, manifest_sha256="d" * 64,
                                   snapshot_date="2026-10-18")
    state = map_inputs.TableState(table=table, reason=None)

    result = score_country("FIN", state=state)

    assert result["esg"] is None
    assert result["esg_status"] == "incomplete"
    assert result["gov_effectiveness"] is None
    assert "gov_effectiveness" in result["missing_inputs"]
    assert "gov_effectiveness" not in result["inputs"]
    assert result["inputs"]["gini_index"]["source"].startswith("World Bank SI.POV.GINI")
    assert result["inputs"]["gini_index"]["year"] == fin["inputs"]["gini_index"]["year"]


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
