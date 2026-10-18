"""The map score contract (Council decision C8, part 2). Offline.

One score per country, computed from pinned official inputs:

- each of the five required inputs is in the pinned table, fresh at the date of use,
  inside its scale, and sourced, or it is missing with a reason;
- ok when all five qualify, incomplete (score null) otherwise;
- no default constant and no other source is ever substituted for a missing input;
- /map/countries and /map/countries/{code} share one function and agree.

The scoring rules run on in-memory tables built here. The hash and file handling
are tested in tests/test_map_inputs.py.
"""
import math
from datetime import date, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.external_data as ed
from app import map_inputs
from app.api import map_data
from app.api.map_data import METHODOLOGY_VERSION, REQUIRED_INPUTS, score_country

AS_OF = date(2026, 10, 19)
SOURCES = {
    "renewable_share": ("World Bank EG.FEC.RNEW.ZS (World Development Indicators)", "% (of total final energy consumption)", "2023"),
    "co2_per_capita": ("World Bank EN.GHG.CO2.PC.CE.AR5 (World Development Indicators)", "t CO2 per capita", "2024"),
    "gini_index": ("World Bank SI.POV.GINI (World Development Indicators)", "index, 0-100 (survey-based, sparse years)", "2022"),
    "gov_effectiveness": ("World Bank GOV_WGI_GE.EST (Worldwide Governance Indicators)", "WGI estimate (approx. -2.5 to +2.5)", "2025"),
    "hdi": ("UNDP Human Development Report composite indices time series", "index, 0-1", "2023"),
}
VALUES = {"renewable_share": 40.0, "co2_per_capita": 5.0, "gini_index": 30.0, "gov_effectiveness": 1.0, "hdi": 0.9}


def _entry(key, value=None, **overrides):
    source, unit, year = SOURCES[key]
    entry = {"value": VALUES[key] if value is None else value, "unit": unit, "year": year,
             "source": source, "release": "release 2026", "fetched_at": "2026-10-18T13:00:00Z"}
    entry.update(overrides)
    return entry


def _row(iso3="TST", *, drop=(), **values):
    """A table row with every input present, except the keys named in drop. Values override VALUES."""
    inputs = {}
    for key in REQUIRED_INPUTS:
        if key in drop:
            continue
        inputs[key] = _entry(key, values[key]) if key in values else _entry(key)
    return {"iso3": iso3, "complete": not drop, "inputs": inputs, "missing": list(drop)}


def _state(*rows):
    table = map_inputs.PinnedTable(
        rows={r["iso3"]: r for r in rows}, table_sha256="a" * 64, manifest_sha256="b" * 64,
        snapshot_date="2026-10-18",
    )
    return map_inputs.TableState(table=table, reason=None)


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(map_inputs, "use_date", lambda: AS_OF)
    return AS_OF


def _client() -> TestClient:
    api = FastAPI()
    api.include_router(map_data.router)
    return TestClient(api)


# ---------- each required input ----------

@pytest.mark.parametrize("key", REQUIRED_INPUTS)
def test_each_required_input_missing_makes_the_score_null(clock, key):
    assert score_country("TST", state=_state(_row()))["esg_status"] == "ok", "complete before one input is removed"

    result = score_country("TST", state=_state(_row(drop=(key,))))

    assert result["esg"] is None
    assert result["esg_status"] == "incomplete"
    assert result["missing_inputs"] == [key]
    assert key not in result["inputs"]
    assert result["esg_source"] is None
    assert result["esg_breakdown"] is None


# ---------- None, NaN, infinities and non-numbers ----------

BAD_VALUES = [float("nan"), float("inf"), float("-inf"), "45.0", True, [40.0]]


@pytest.mark.parametrize("bad", BAD_VALUES, ids=repr)
def test_an_invalid_value_is_missing(clock, bad):
    result = score_country("TST", state=_state(_row(renewable_share=bad)))

    assert result["esg"] is None
    assert result["esg_status"] == "incomplete"
    assert "renewable_share" in result["missing_inputs"]


def test_a_null_value_is_missing_with_the_no_observation_reason(clock):
    row = _row()
    row["inputs"]["gini_index"] = {"value": None, "reason": "no_observation"}

    result = score_country("TST", state=_state(row))

    assert result["esg"] is None
    assert result["input_notes"]["gini_index"] == "no observation in the pinned table"


# ---------- a measured zero and an absent input ----------

def test_a_measured_zero_counts_as_measured(clock):
    result = score_country("TST", state=_state(_row(gov_effectiveness=0.0)))

    assert "gov_effectiveness" not in result["missing_inputs"]
    assert result["inputs"]["gov_effectiveness"]["value"] == 0.0


def test_an_absent_input_is_missing_not_zero(clock):
    """C8: the old code substituted 0.0 for a missing gov_effectiveness. It is now missing."""
    result = score_country("TST", state=_state(_row(drop=("gov_effectiveness",))))

    assert "gov_effectiveness" in result["missing_inputs"]
    assert "gov_effectiveness" not in result["inputs"]
    assert result["gov_effectiveness"] is None
    assert result["esg"] is None


def test_a_value_with_no_stated_source_is_incomplete(clock):
    row = _row()
    row["inputs"]["gov_effectiveness"]["source"] = None

    result = score_country("TST", state=_state(row))

    assert result["esg"] is None
    assert "no source or release" in result["input_notes"]["gov_effectiveness"]


# ---------- ok, sourcing and the formula ----------

def test_all_five_pinned_inputs_give_ok_with_the_pinned_source(clock):
    result = score_country("TST", state=_state(_row()))

    assert result["esg_status"] == "ok"
    assert result["esg_source"] == "pinned_table"
    assert result["missing_inputs"] == []
    assert result["esg"] == 68  # the formula, hand-computed from these five values: unchanged from v2
    for key in REQUIRED_INPUTS:
        assert result["inputs"][key]["source"] == SOURCES[key][0]
        assert result["inputs"][key]["year"] == SOURCES[key][2]
        assert result["inputs"][key]["release"] == "release 2026"
        assert result["inputs"][key]["fetched_at"] == "2026-10-18T13:00:00Z"
        assert result["inputs"][key]["age_years"] == 2026 - int(SOURCES[key][2])


def test_the_response_names_the_table_and_the_methodology(clock):
    result = score_country("TST", state=_state(_row()))

    assert result["methodology_version"] == METHODOLOGY_VERSION == "map-esg-v3"
    assert result["inputs_table_sha256"] == "a" * 64
    assert result["inputs_manifest_sha256"] == "b" * 64
    assert result["age_reference_date"] == "2026-10-19"


def test_the_live_cache_does_not_change_the_score(clock, monkeypatch):
    """Live cache guard. A fresh entry that disagrees with the table is ignored, not blended."""
    monkeypatch.setattr(ed, "_live_cache", {"Testland": {
        "country": "Testland", "indicators": {"renewable_share": 99.0}, "indicator_sources": {"renewable_share": "world_bank"},
        "indicator_periods": {"renewable_share": "2026"}}})
    monkeypatch.setattr(ed, "_cache_timestamps", {"Testland": datetime.now()})

    result = score_country("TST", state=_state(_row()))

    assert result["inputs"]["renewable_share"]["value"] == 40.0
    assert result["inputs"]["renewable_share"]["source"] == SOURCES["renewable_share"][0]
    assert result["esg"] == 68


# ---------- age at the date of use ----------

def test_a_value_too_old_at_the_date_of_use_is_missing(monkeypatch):
    monkeypatch.setattr(map_inputs, "use_date", lambda: date(2031, 1, 1))

    result = score_country("TST", state=_state(_row()))

    assert result["esg"] is None
    # Fixture years: renewables 2023 (age 8, limit 5), co2 2024 (7, limit 5), gini 2022 (9, limit 10),
    # gov 2025 (6, limit 3), hdi 2023 (8, limit 3). Only gini is still inside its window at 2031.
    assert set(result["missing_inputs"]) == {"renewable_share", "co2_per_capita", "gov_effectiveness", "hdi"}
    assert "too old: 8 years at 2031-01-01, the limit is 3" == result["input_notes"]["hdi"]


# ---------- no row, no table ----------

def test_a_country_with_no_row_has_no_inputs_and_no_default(clock):
    result = score_country("NOW", state=_state(_row()))

    assert result["inputs"] == {}
    assert set(result["missing_inputs"]) == set(REQUIRED_INPUTS)
    assert result["esg"] is None
    assert all(result[k] is None for k in ("hdi", "gini_index", "gov_effectiveness"))
    assert result["input_notes"]["hdi"] == "no row for this country in the pinned table"


def test_a_missing_table_is_incomplete_with_its_reason(clock):
    result = score_country("TST", state=map_inputs.TableState(table=None, reason="gone"))

    assert result["esg"] is None
    assert result["inputs_table_sha256"] is None
    assert result["input_notes"]["hdi"] == "pinned input table unavailable: gone"


# ---------- list == detail, nulls, and counts ----------

@pytest.fixture
def shipped(clock):
    """The process's real table, with the clock at the day it was written."""
    map_inputs.reset_table_cache()
    yield
    map_inputs.reset_table_cache()


def test_list_and_detail_agree_for_every_country(shipped):
    client = _client()
    listed = {c["code"]: c for c in client.get("/map/countries").json()["countries"]}

    assert len(listed) == 33
    for code in listed:
        detail = client.get(f"/map/countries/{code}").json()
        for field in ("esg", "esg_status", "esg_source", "missing_inputs", "input_notes", "inputs",
                      "esg_breakdown", "methodology_version", "reference_profile", "age_reference_date",
                      "inputs_table_sha256", "inputs_manifest_sha256", "renewable_share_pct",
                      "co2_intensity_t_per_capita"):
            assert detail[field] == listed[code][field], (code, field)


def test_list_and_detail_agree_when_the_clock_moves(shipped, monkeypatch):
    """A shared calculation gives the same answer for the same date, whichever route asks."""
    monkeypatch.setattr(map_inputs, "use_date", lambda: date(2029, 6, 1))
    client = _client()
    listed = {c["code"]: c for c in client.get("/map/countries").json()["countries"]}

    for code in listed:
        detail = client.get(f"/map/countries/{code}").json()
        assert detail["esg"] == listed[code]["esg"], code
        assert detail["missing_inputs"] == listed[code]["missing_inputs"], code
    assert all(c["esg"] is None for c in listed.values())


def test_a_null_score_sorts_last_and_is_never_zero(shipped):
    body = _client().get("/map/countries").json()

    scored = [c for c in body["countries"] if c["esg"] is not None]
    assert len(scored) == 32
    assert body["countries"][-1]["code"] == "NZL"
    assert all(c["esg"] is None for c in body["countries"][32:])
    assert all(c["esg"] != 0 for c in body["countries"])


def test_status_counts_are_consistent(shipped):
    body = _client().get("/map/countries").json()
    counts = body["status_counts"]

    assert set(counts) == {"ok", "incomplete"}
    assert sum(counts.values()) == body["total_countries"] == 33
    assert body["scored_countries"] == counts["ok"] == 32


def test_the_list_reports_the_table_it_was_scored_from(shipped):
    body = _client().get("/map/countries").json()

    assert body["inputs_table_sha256"] == "3832f89f1f1dc4e32120aee189089b7b459278e1b89e273a7da4413ddcd1fead"
    assert body["inputs_manifest_sha256"] == map_inputs.load_table().table.manifest_sha256
    assert body["methodology_version"] == "map-esg-v3"


def test_reference_profile_is_separate_and_never_the_score(clock):
    """ISL's hand-entered profile is 86. Its score comes from the table: here 68, not 86."""
    client_state = _state(_row("ISL"))
    result = score_country("ISL", state=client_state)

    assert result["esg"] == 68
    assert "esg_score" not in result
    assert next(c for c in map_data.COUNTRIES if c["code"] == "ISL")["esg"] == 86


def test_physical_fields_are_the_table_values_when_they_qualify(clock):
    result = score_country("TST", state=_state(_row(renewable_share=45.0, co2_per_capita=6.9)))

    assert result["renewable_share_pct"] == 45.0
    assert result["co2_intensity_t_per_capita"] == 6.9


def test_physical_fields_are_null_when_the_input_is_missing(clock):
    result = score_country("TST", state=_state(_row(drop=("co2_per_capita",))))

    assert result["co2_intensity_t_per_capita"] is None
    assert result["renewable_share_pct"] == 40.0


def test_the_finite_helper_refuses_bool_and_non_finite():
    assert map_inputs._finite(True) is None
    assert map_inputs._finite(float("nan")) is None
    assert map_inputs._finite(0) == 0.0
    assert math.isfinite(map_inputs._finite(3))
