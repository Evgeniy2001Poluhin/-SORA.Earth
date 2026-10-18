"""The map looks countries up by ISO3 code, so South Korea and Czechia resolve.

Before this, `_NAME_MAP` sent "South Korea" to "Korea" and "Czech Republic" to
"Czechia". Neither name has a row in BENCHMARKS, so both countries were always
scored as hand-entered profiles with no data behind them.

Korea now resolves to its BENCHMARKS row. Czechia resolves to the map's own name,
but BENCHMARKS has no Czech row yet; that is a recorded gap until the inputs from
the snapshot are wired in (part 2).
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.external_data as ed
from app.api import map_data
from app.country_benchmarks import BENCHMARKS
from app.external_data import COUNTRY_ISO3

#: Map countries with no BENCHMARKS row under their lookup name. Each is scored from
#: its hand-entered profile. Pinned so that adding a row, or losing one, fails here.
NO_BENCHMARK_ROW = {"ISL", "BEL", "PRT", "POL", "CZE", "CHL", "NZL", "KEN", "ARE"}


@pytest.fixture
def client(monkeypatch):
    """The map router alone, with an empty live cache: every score comes from BENCHMARKS or the profile."""
    monkeypatch.setattr(ed, "_live_cache", {})
    monkeypatch.setattr(ed, "_cache_timestamps", {})
    api = FastAPI()
    api.include_router(map_data.router)
    return TestClient(api)


def _by_code(client):
    return {c["code"]: c for c in client.get("/map/countries").json()["countries"]}


def test_korea_resolves_to_its_benchmark_row():
    name = map_data._lookup_name("KOR", "South Korea")
    assert name == "South Korea"
    assert name in BENCHMARKS
    assert COUNTRY_ISO3[name] == "KOR"


def test_czechia_resolves_to_the_map_name_not_to_a_display_alias():
    name = map_data._lookup_name("CZE", "Czech Republic")
    assert name == "Czech Republic"
    assert name != "Czechia"


def test_no_map_country_is_looked_up_under_a_name_the_tables_do_not_use():
    """The lookup name for each map country is one the map offers, not an alias invented for the map."""
    for country in map_data.COUNTRIES:
        name = map_data._lookup_name(country["code"], country["name"])
        assert name not in {"Korea", "Czechia"}, country


def test_the_map_countries_without_a_benchmark_row_are_exactly_the_recorded_gap():
    missing = {
        c["code"] for c in map_data.COUNTRIES
        if map_data._lookup_name(c["code"], c["name"]) not in BENCHMARKS
    }
    assert missing == NO_BENCHMARK_ROW


def test_korea_is_scored_from_its_benchmark_row_on_the_map(client):
    korea = _by_code(client)["KOR"]
    assert korea["esg_source"] == map_data.ESG_SOURCE_STATIC
    assert korea["esg_source"] != map_data.ESG_SOURCE_HARDCODED


def test_czechia_still_has_no_data_and_says_so(client):
    czechia = _by_code(client)["CZE"]
    assert czechia["esg_source"] == map_data.ESG_SOURCE_HARDCODED
