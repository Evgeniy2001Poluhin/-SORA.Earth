"""The map looks countries up by ISO3 code, so South Korea and Czechia resolve.

Carried from the C8 part 2 patch. Before, `_NAME_MAP` sent "South Korea" to "Korea"
and "Czech Republic" to "Czechia". Neither name had a row in BENCHMARKS, so both
countries were always scored from hand-entered profiles.

The pinned table is keyed by ISO3 and holds a row for every map country, so the
lookup needs no display-name alias at all. These tests pin that: KOR and CZE resolve
to their own rows, and a display name is not a key.
"""
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import map_inputs
from app.api import map_data


@pytest.fixture
def shipped(monkeypatch):
    monkeypatch.setattr(map_inputs, "use_date", lambda: date(2026, 10, 19))
    monkeypatch.setattr(map_inputs, "_STATE", {})
    yield


def _by_code(client):
    return {c["code"]: c for c in client.get("/map/countries").json()["countries"]}


def _client():
    api = FastAPI()
    api.include_router(map_data.router)
    return TestClient(api)


def test_every_map_country_has_a_pinned_row_under_its_iso3_code(shipped):
    rows = map_inputs.current_table_state().table.rows
    missing = [c["code"] for c in map_data.COUNTRIES if c["code"] not in rows]

    assert missing == []


def test_korea_resolves_to_its_pinned_row_by_iso3(shipped):
    rows = map_inputs.current_table_state().table.rows
    result = map_data.score_country("KOR")

    assert result["esg_status"] == "ok"
    for key, provenance in result["inputs"].items():
        assert provenance["value"] == rows["KOR"]["inputs"][key]["value"], key


def test_czechia_resolves_to_its_pinned_row_by_iso3(shipped):
    rows = map_inputs.current_table_state().table.rows
    result = map_data.score_country("CZE")

    assert result["esg_status"] == "ok"
    assert result["inputs"]["hdi"]["value"] == rows["CZE"]["inputs"]["hdi"]["value"]


def test_a_display_name_is_not_a_lookup_key(shipped):
    """A display alias gets no row. That is what made KOR and CZE unresolvable before."""
    for alias in ("South Korea", "Korea", "Czech Republic", "Czechia"):
        result = map_data.score_country(alias)
        assert result["esg"] is None, alias
        assert result["inputs"] == {}, alias


def test_the_list_scores_korea_and_czechia_from_the_table(shipped):
    by_code = _by_code(_client())

    for code in ("KOR", "CZE"):
        assert by_code[code]["esg_status"] == "ok", code
        assert by_code[code]["esg_source"] == "pinned_table", code
