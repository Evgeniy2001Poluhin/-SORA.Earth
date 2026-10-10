"""A country the product offers is scored on its own data, or the gap is recorded.

`calculate_esg` reads the country's figures with
`BENCHMARKS.get(country, GLOBAL_AVG)`, by exact name. `COUNTRIES` in
`app/main.py` is the list the interface offers and `/evaluate` accepts. The two
tables are maintained by hand and nothing compared them, so three of the
twenty-seven selectable countries silently fell through to the world average:

    United States   -- the table has the same data under "USA"
    United Kingdom  -- the table has the same data under "UK"
    Finland         -- the table had no Finland under any spelling (fixed in finding 30.6)

The first two are a spelling mismatch over data that exists. Measured on one
project (budget 150 000, CO2 340 t/yr, social 9, 18 months):

    United States   68.41 on the world average, 59.36 on its own figures
    United Kingdom  71.71 on the world average, 78.59 on its own figures

Nine points high for one country and seven low for the other, on the score the
product exists to produce -- and the response names the country the caller
asked for, so nothing on screen says the figures behind it are the world's.

Finland's row now exists in BENCHMARKS with values from World Bank and UNDP
(see SOURCES in app/country_benchmarks.py): co2_per_capita 5.5, gdp_per_capita 53156,
hdi 0.942, renewable_share 52.9, gini_index 27.4, esg_rank null, gov_effectiveness null.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.country_benchmarks import BENCHMARKS, GLOBAL_AVG

#: Selectable countries with no benchmark of their own, and why. An entry here
#: is a recorded gap: the score for that country is the world average.
#: Finland was the only entry and was fixed in finding 30.6.
KNOWN_WITHOUT_BENCHMARK = {}

client = TestClient(main.app, raise_server_exceptions=False)
PROJECT = {"budget": 150000, "co2_reduction": 340, "social_impact": 9, "duration_months": 18}


def test_the_two_tables_are_both_populated():
    """Neither list may be empty, or everything below passes vacuously."""
    assert len(main.COUNTRIES) >= 20, len(main.COUNTRIES)
    assert len(BENCHMARKS) >= 20, len(BENCHMARKS)


def test_every_selectable_country_has_its_own_benchmark():
    missing = sorted(set(main.COUNTRIES) - set(BENCHMARKS) - set(KNOWN_WITHOUT_BENCHMARK))
    assert not missing, (
        "these countries are offered by the interface and have no benchmark, so "
        "`calculate_esg` scores them on GLOBAL_AVG while the response names the "
        "country the caller asked for: " + ", ".join(missing)
    )


@pytest.mark.parametrize("gap, reason", sorted(KNOWN_WITHOUT_BENCHMARK.items()))
def test_a_recorded_gap_is_still_a_gap(gap, reason):
    """An allowance that no longer matches anything is a stale rule."""
    assert gap in main.COUNTRIES, f"{gap} is no longer selectable; drop the allowance"
    assert gap not in BENCHMARKS, (
        f"{gap} now has a benchmark, so the allowance ({reason}) is out of date "
        f"and should be removed"
    )


@pytest.mark.parametrize("country", ["United States", "United Kingdom"])
def test_a_country_whose_data_exists_is_scored_on_it(country):
    answer = client.post("/api/v1/evaluate", json={**PROJECT, "region": country})
    assert answer.status_code == 200, answer.text
    named = answer.json()["country_benchmark"]["country"]
    assert named == country, (
        f"a project in {country} was scored against {named!r}. Its figures are in "
        f"BENCHMARKS under a shorter spelling, and the lookup is by exact name."
    )


def test_the_global_fallback_still_works():
    """Control: the path this is about still works where it always worked."""
    from app.schemas import ProjectInput
    from app.main import calculate_esg

    proj = ProjectInput(**PROJECT, region="Atlantis")
    result = calculate_esg(proj, region_name="Europe")
    assert result["environment_score"] > 0
