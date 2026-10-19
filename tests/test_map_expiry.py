"""The map says when its pinned data stops being usable, and why a country is incomplete. Offline.

Council decision C8, part 3. Every number here is read through the API or the scoring
function with the clock frozen. The shipped table is 2026-10-18; renewables and HDI
expire on 1 January 2027, the earliest date any value in the table is too old.
"""
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import map_inputs
from app.api import map_data


@pytest.fixture
def clock(monkeypatch):
    box = {"today": date(2026, 10, 19)}
    monkeypatch.setattr(map_inputs, "use_date", lambda: box["today"])
    monkeypatch.setattr(map_inputs, "_STATE", {})
    return box


def _client() -> TestClient:
    api = FastAPI()
    api.include_router(map_data.router)
    return TestClient(api)


def _listing(client):
    return client.get("/map/countries").json()


# ---------- expires_at ----------

def test_a_value_expires_on_the_first_day_after_its_last_permitted_year():
    """expires_at = 1 January of year + max_age + 1. Renewables 2021 with a limit of 5 expire on 2027-01-01."""
    assert map_inputs.expires_at(2021, 5) == date(2027, 1, 1)
    assert map_inputs.expires_at(2023, 3) == date(2027, 1, 1)
    assert map_inputs.expires_at(2024, 5) == date(2030, 1, 1)


def test_the_last_usable_day_is_31_december_and_the_first_unusable_is_1_january(clock):
    doc = map_inputs.current_table_state().table.rows["DEU"]
    assert doc["inputs"]["renewable_share"]["year"] == "2021"

    clock["today"] = date(2026, 12, 31)
    last = map_data.score_country("DEU")
    assert "renewable_share" not in last["missing_inputs"]
    assert last["inputs"]["renewable_share"]["expires_at"] == "2027-01-01"
    assert last["inputs"]["renewable_share"]["max_age_years"] == 5

    clock["today"] = date(2027, 1, 1)
    first = map_data.score_country("DEU")
    assert first["input_reasons"]["renewable_share"]["code"] == "too_old"


def test_the_earliest_expiry_of_the_shipped_table_is_2027_01_01(clock):
    assert map_inputs.earliest_expiry(map_inputs.current_table_state().table) == date(2027, 1, 1)


# ---------- the two reasons ----------

def test_a_missing_observation_is_no_observation_with_no_year(clock):
    reason = map_data.score_country("NZL")["input_reasons"]["gini_index"]

    assert reason["code"] == "no_observation"
    assert "year" not in reason and "max_age_years" not in reason
    assert reason["text"] == "no observation in the pinned table"


def test_an_observation_past_its_limit_is_too_old_with_year_age_and_limit(clock):
    clock["today"] = date(2027, 1, 1)

    reason = map_data.score_country("DEU")["input_reasons"]["hdi"]

    assert reason["code"] == "too_old"
    assert reason["year"] == "2023"
    assert reason["age_years"] == 4
    assert reason["max_age_years"] == 3
    assert reason["expires_at"] == "2027-01-01"
    assert reason["text"] == "too old: 4 years at 2027-01-01, the limit is 3"


def test_the_two_reasons_are_distinct_codes_and_input_notes_keeps_the_sentence(clock):
    clock["today"] = date(2027, 1, 1)
    result = map_data.score_country("NZL")

    assert result["input_reasons"]["gini_index"]["code"] == "no_observation"
    assert result["input_reasons"]["hdi"]["code"] == "too_old"
    for key, reason in result["input_reasons"].items():
        assert result["input_notes"][key] == reason["text"], key
    assert set(result["input_reasons"]) == set(result["missing_inputs"])


# ---------- 2027-01-01: everything incomplete, with too_old ----------

def test_on_1_january_2027_every_country_is_incomplete_with_too_old_renewables_and_hdi(clock):
    clock["today"] = date(2027, 1, 1)
    body = _listing(_client())

    assert body["scored_countries"] == 0
    assert body["status_counts"] == {"ok": 0, "incomplete": 33}
    for country in body["countries"]:
        assert country["esg"] is None, country["code"]
        assert country["input_reasons"]["renewable_share"]["code"] == "too_old", country["code"]
        assert country["input_reasons"]["hdi"]["code"] == "too_old", country["code"]


def test_on_1_january_2027_the_notice_is_on_and_the_days_are_zero(clock):
    clock["today"] = date(2027, 1, 1)
    body = _listing(_client())

    assert body["earliest_expiry"] == "2027-01-01"
    assert body["days_to_earliest_expiry"] == 0
    assert body["expiry_notice"] is True


# ---------- the notice threshold: 60 days ----------

@pytest.mark.parametrize("today,days,notice", [
    (date(2026, 10, 19), 74, False),   # the day this was written: 74 days out
    (date(2026, 11, 1), 61, False),    # one day outside the notice
    (date(2026, 11, 2), 60, True),     # exactly 60 days: inside
    (date(2026, 11, 3), 59, True),
    (date(2027, 1, 2), -1, True),      # already passed: still shown
])
def test_the_notice_turns_on_at_60_days_from_the_earliest_expiry(clock, today, days, notice):
    clock["today"] = today
    body = _listing(_client())

    assert body["days_to_earliest_expiry"] == days
    assert body["expiry_notice"] is notice
    assert body["expiry_notice_days"] == 60


def test_the_frozen_clock_drives_the_notice_not_the_wall_clock(clock):
    clock["today"] = date(2026, 1, 1)
    assert _listing(_client())["expiry_notice"] is False


# ---------- the response says what the data is ----------

def test_the_listing_names_the_snapshot_the_limits_and_the_expiry(clock):
    body = _listing(_client())

    assert body["snapshot_date"] == "2026-10-18"
    assert body["max_age_years"] == map_inputs.MAX_AGE_YEARS
    assert body["table_available"] is True
    assert body["table_unavailable_reason"] is None
    assert body["earliest_expiry"] == "2027-01-01"


def test_each_country_carries_its_snapshot_limits_and_its_own_earliest_expiry(clock):
    client = _client()
    deu = client.get("/map/countries/DEU").json()
    nzl = client.get("/map/countries/NZL").json()

    assert deu["snapshot_date"] == "2026-10-18"
    assert deu["max_age_years"] == map_inputs.MAX_AGE_YEARS
    assert deu["earliest_expiry"] == "2027-01-01"
    assert deu["inputs"]["hdi"]["expires_at"] == "2027-01-01"
    assert deu["inputs"]["co2_per_capita"]["expires_at"] == "2030-01-01"
    assert nzl["earliest_expiry"] == "2027-01-01"


def test_without_a_table_every_reason_is_table_unavailable_and_there_is_no_notice(clock, monkeypatch):
    monkeypatch.setattr(map_inputs, "_STATE", {"state": map_inputs.TableState(table=None, reason="gone")})

    body = _listing(_client())

    assert body["table_available"] is False
    assert body["table_unavailable_reason"] == "gone"
    assert body["snapshot_date"] is None
    assert body["earliest_expiry"] is None
    assert body["expiry_notice"] is False
    for country in body["countries"]:
        assert all(r["code"] == "table_unavailable" for r in country["input_reasons"].values()), country["code"]
