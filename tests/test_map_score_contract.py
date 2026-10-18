"""The map score contract (Council decision C8). Offline.

One score per country, computed from the inputs actually used:

- each of the five required inputs is measured, a stated static reference, or missing;
- ok when all five are measured, mixed when all five qualify and some are static,
  incomplete (score null) otherwise;
- no default constant is ever substituted for a missing input;
- /map/countries and /map/countries/{code} share one function and agree.
"""
import math
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.external_data as ed
from app.api import map_data
from app.api.map_data import METHODOLOGY_VERSION, REQUIRED_INPUTS, score_country
from app.country_benchmarks import BENCHMARKS

FETCHED = datetime(2026, 10, 1, 12, 0, 0)

def _client() -> TestClient:
    api = FastAPI()
    api.include_router(map_data.router)
    return TestClient(api)


def _record(bench=None, live=None, sources=None, periods=None):
    """What get_merged_country_data returns: BENCHMARKS keys at top level, plus a live block."""
    d = dict(bench or {})
    if live is not None:
        d["live"] = {
            "country": "Testland",
            "indicators": dict(live),
            "indicator_sources": dict(sources or {}),
            "indicator_periods": dict(periods or {}),
        }
    return d


@pytest.fixture
def fake(monkeypatch):
    """Replace the data layer. Set records by country name; an unset name has no data."""
    state = {"records": {}, "fetched": FETCHED}
    monkeypatch.setattr(map_data, "get_merged_country_data",
                        lambda name: state["records"].get(name, {}))
    monkeypatch.setattr(map_data, "live_entry_fetched_at", lambda name: state["fetched"])
    return state


def _all_measured_record():
    """Three inputs measured live; co2 and hdi static, both with a stated source."""
    return _record(
        bench={"co2_per_capita": 5.0, "hdi": 0.9},
        live={"renewable_share": 40.0, "gini_index": 30.0, "gov_effectiveness": 1.0},
        sources={"renewable_share": "world_bank", "gini_index": "world_bank",
                 "gov_effectiveness": "world_bank"},
        periods={"renewable_share": "2023", "gini_index": "2022", "gov_effectiveness": "2024"},
    )


# ---------- each required input ----------

@pytest.mark.parametrize("key", REQUIRED_INPUTS)
def test_each_required_input_missing_makes_the_score_null(fake, key):
    fake["records"]["Testland"] = _all_measured_record()
    assert score_country("Testland")["esg_status"] == "mixed", "complete before one input is removed"

    record = _all_measured_record()
    record.pop(key, None)
    record["live"]["indicators"].pop(key, None)
    record["live"]["indicator_sources"].pop(key, None)
    fake["records"]["Testland"] = record

    result = score_country("Testland")
    assert result["esg"] is None
    assert result["esg_status"] == "incomplete"
    assert result["missing_inputs"] == [key]
    assert key not in result["inputs"]
    assert result["esg_source"] is None
    assert result["esg_breakdown"] is None


# ---------- None, NaN, infinities and non-numbers ----------

BAD_VALUES = [None, float("nan"), float("inf"), float("-inf"), "45.0", True, [40.0]]


@pytest.mark.parametrize("bad", BAD_VALUES, ids=repr)
def test_an_invalid_measured_value_is_missing(fake, bad):
    record = _all_measured_record()
    record["live"]["indicators"]["renewable_share"] = bad
    fake["records"]["Testland"] = record

    result = score_country("Testland")

    assert result["esg"] is None
    assert result["esg_status"] == "incomplete"
    assert "renewable_share" in result["missing_inputs"]


@pytest.mark.parametrize("bad", BAD_VALUES, ids=repr)
def test_an_invalid_static_value_is_missing(fake, bad):
    record = _all_measured_record()
    record["co2_per_capita"] = bad  # static: SOURCES states its source, so only the value is wrong
    fake["records"]["Testland"] = record

    result = score_country("Testland")

    assert result["esg"] is None
    assert "co2_per_capita" in result["missing_inputs"]


# ---------- a measured zero and a defaulted zero ----------

def test_a_measured_zero_counts_as_measured(fake):
    record = _all_measured_record()
    record["live"]["indicators"]["gov_effectiveness"] = 0.0
    record["live"]["indicator_sources"]["gov_effectiveness"] = "world_bank"
    fake["records"]["Testland"] = record

    result = score_country("Testland")

    assert "gov_effectiveness" not in result["missing_inputs"]
    assert result["inputs"]["gov_effectiveness"]["value"] == 0.0
    assert result["inputs"]["gov_effectiveness"]["kind"] == "measured"


def test_a_defaulted_zero_is_missing_not_zero(fake):
    """No gov_effectiveness anywhere. The old code substituted 0.0; C8 names it missing."""
    record = _all_measured_record()
    record["live"]["indicators"].pop("gov_effectiveness")
    record["live"]["indicator_sources"].pop("gov_effectiveness")
    fake["records"]["Testland"] = record

    result = score_country("Testland")

    assert "gov_effectiveness" in result["missing_inputs"]
    assert "gov_effectiveness" not in result["inputs"]
    assert result["gov_effectiveness"] is None
    assert result["esg"] is None


def test_a_zero_tagged_as_a_global_average_default_is_not_a_measurement(fake):
    """GLOBAL_AVG holds gov_effectiveness 0.0. Offline, the live cache can carry it tagged global_avg."""
    record = _all_measured_record()
    record["live"]["indicators"]["gov_effectiveness"] = 0.0
    record["live"]["indicator_sources"]["gov_effectiveness"] = "global_avg"
    fake["records"]["Testland"] = record

    result = score_country("Testland")

    assert "gov_effectiveness" in result["missing_inputs"]
    assert "gov_effectiveness" not in result["inputs"]


def test_a_zero_from_a_stated_source_record_counts(fake):
    """A static 0.0 from a record with a stated source is data, not a default."""
    record = _all_measured_record()
    record["co2_per_capita"] = 0.0  # SOURCES states World Bank 2024 for co2
    fake["records"]["Testland"] = record

    result = score_country("Testland")

    assert "co2_per_capita" not in result["missing_inputs"]
    assert result["inputs"]["co2_per_capita"]["value"] == 0.0
    assert result["inputs"]["co2_per_capita"]["source"] == "World Bank"


# ---------- ok, mixed, and sourcing ----------

def test_all_inputs_measured_gives_ok(fake):
    """Synthetic: production never fetches hdi live. Here every input is measured."""
    fake["records"]["Testland"] = _record(
        live={"renewable_share": 40.0, "co2_per_capita": 5.0, "gini_index": 30.0,
              "gov_effectiveness": 1.0, "hdi": 0.9},
        sources={"renewable_share": "world_bank", "co2_per_capita": "oecd", "gini_index": "world_bank",
                 "gov_effectiveness": "world_bank", "hdi": "world_bank"},
    )

    result = score_country("Testland")

    assert result["esg_status"] == "ok"
    assert result["esg_source"] == "world_bank_live"
    assert result["missing_inputs"] == []
    assert all(v["kind"] == "measured" for v in result["inputs"].values())
    assert result["esg"] == 68  # hand-computed from the formula for these five values


def test_partly_static_gives_mixed(fake):
    fake["records"]["Testland"] = _all_measured_record()

    result = score_country("Testland")

    assert result["esg_status"] == "mixed"
    assert result["esg_source"] == "mixed"
    assert result["inputs"]["hdi"]["kind"] == "static"
    assert result["inputs"]["hdi"]["source"] == "UNDP Human Development Report"
    assert result["inputs"]["hdi"]["period"] == "2022"
    assert result["inputs"]["hdi"]["fetched_at"] is None
    assert isinstance(result["esg"], int)


def test_a_static_value_with_no_stated_source_is_incomplete(fake):
    """gov_effectiveness in BENCHMARKS has no source in SOURCES: it cannot back a mixed score."""
    fake["records"]["Testland"] = _record(
        bench={"co2_per_capita": 5.0, "hdi": 0.9, "gov_effectiveness": 1.0},
        live={"renewable_share": 40.0, "gini_index": 30.0},
        sources={"renewable_share": "world_bank", "gini_index": "world_bank"},
    )

    result = score_country("Testland")

    assert result["esg_status"] == "incomplete"
    assert "gov_effectiveness" in result["missing_inputs"]
    assert "no stated source" in result["input_notes"]["gov_effectiveness"]


def test_finland_gini_qualifies_on_its_stated_source(fake):
    """Finland's gini is World Bank SI.POV.GINI 2023 (SOURCE_EXCEPTIONS). Germany's is not stated."""
    fake["records"]["Finland"] = _record(
        bench={"gini_index": 27.4, "co2_per_capita": 5.0, "hdi": 0.9},
        live={"renewable_share": 40.0, "gov_effectiveness": 1.0},
        sources={"renewable_share": "world_bank", "gov_effectiveness": "world_bank"},
    )

    result = score_country("Finland")

    assert result["inputs"]["gini_index"]["source"] == "World Bank"
    assert result["inputs"]["gini_index"]["period"] == "2023"
    assert "gini_index" not in result["missing_inputs"]
    assert map_data._static_provenance("Germany", "gini_index") is None


# ---------- empty and stale cache, against the real cache ----------

@pytest.fixture
def real_cache(monkeypatch):
    live, stamps = {}, {}
    monkeypatch.setattr(ed, "_live_cache", live)
    monkeypatch.setattr(ed, "_cache_timestamps", stamps)
    return live, stamps


def _put_live(cache, name, *, age_hours, indicators, sources):
    live, stamps = cache
    live[name] = {"country": name, "indicators": indicators, "indicator_sources": sources,
                  "indicator_periods": {}}
    stamps[name] = datetime.now() - timedelta(hours=age_hours)


def test_empty_cache_reads_only_stated_static_values(real_cache):
    result = score_country("Germany")

    assert result["esg_status"] == "incomplete"  # gini and gov have no stated source
    assert result["inputs"]["renewable_share"]["kind"] == "static"
    assert result["inputs"]["renewable_share"]["fetched_at"] is None
    assert result["renewable_share_pct"] is None


def test_stale_cache_is_the_same_as_an_empty_one(real_cache):
    empty = score_country("Germany")

    _put_live(real_cache, "Germany", age_hours=48,
              indicators={"renewable_share": 45.0, "gini_index": 31.0, "gov_effectiveness": 1.5},
              sources={"renewable_share": "world_bank", "gini_index": "world_bank",
                       "gov_effectiveness": "world_bank"})
    stale = score_country("Germany")

    assert stale == empty
    assert stale["inputs"]["renewable_share"]["kind"] == "static"
    assert stale["renewable_share_pct"] is None


def test_fresh_cache_supplies_measured_inputs(real_cache):
    _put_live(real_cache, "Germany", age_hours=0,
              indicators={"renewable_share": 45.0, "gini_index": 31.0, "gov_effectiveness": 1.5},
              sources={"renewable_share": "world_bank", "gini_index": "world_bank",
                       "gov_effectiveness": "world_bank"})

    result = score_country("Germany")

    assert result["inputs"]["renewable_share"]["kind"] == "measured"
    assert result["inputs"]["renewable_share"]["fetched_at"] is not None
    assert result["renewable_share_pct"] == 45.0
    # co2 and hdi remain static with stated sources, so the score is mixed, not ok.
    assert result["esg_status"] == "mixed"
    assert isinstance(result["esg"], int)


def test_a_failed_lookup_is_incomplete_not_the_profile_score(monkeypatch):
    def boom(name):
        raise RuntimeError("cache unreadable")

    monkeypatch.setattr(map_data, "get_merged_country_data", boom)

    result = score_country("Iceland")

    assert result["esg"] is None
    assert result["esg_status"] == "incomplete"
    assert set(result["missing_inputs"]) == set(REQUIRED_INPUTS)
    assert result["input_notes"]["hdi"] == "country data lookup failed"


# ---------- list == detail, and nulls ----------

def test_list_and_detail_agree_for_every_country(real_cache):
    _put_live(real_cache, "Germany", age_hours=0,
              indicators={"renewable_share": 45.0, "gini_index": 31.0, "gov_effectiveness": 1.5},
              sources={"renewable_share": "world_bank", "gini_index": "world_bank",
                       "gov_effectiveness": "world_bank"})
    client = _client()
    listed = {c["code"]: c for c in client.get("/map/countries").json()["countries"]}

    assert listed, "the list must not be empty"
    for code in listed:
        detail = client.get(f"/map/countries/{code}").json()
        for field in ("esg", "esg_status", "esg_source", "missing_inputs", "input_notes", "inputs",
                      "esg_breakdown", "methodology_version", "reference_profile"):
            assert detail[field] == listed[code][field], (code, field)


def test_a_null_score_sorts_last_and_is_never_zero(fake):
    fake["records"]["Germany"] = _all_measured_record()
    body = _client().get("/map/countries").json()

    scored = [c for c in body["countries"] if c["esg"] is not None]
    assert [c["code"] for c in scored] == ["DEU"]
    assert body["countries"][0]["code"] == "DEU"
    assert all(c["esg"] is None for c in body["countries"][1:])
    assert all(c["esg"] != 0 for c in body["countries"])


def test_status_counts_are_consistent(real_cache):
    body = _client().get("/map/countries").json()
    counts = body["status_counts"]

    assert sum(counts.values()) == body["total_countries"]
    assert body["scored_countries"] == counts["ok"] + counts["mixed"]


# ---------- fields and versions ----------

def test_methodology_version_is_present_everywhere(real_cache):
    client = _client()
    body = client.get("/map/countries").json()

    assert body["methodology_version"] == METHODOLOGY_VERSION == "map-esg-v2"
    assert all(c["methodology_version"] == METHODOLOGY_VERSION for c in body["countries"])
    assert client.get("/map/countries/DEU").json()["methodology_version"] == METHODOLOGY_VERSION


def test_every_input_value_is_the_stated_record_not_a_default(real_cache):
    """On the real data, each input value equals its BENCHMARKS value, so no default can appear."""
    body = _client().get("/map/countries").json()

    for c in body["countries"]:
        record = BENCHMARKS.get(map_data._NAME_MAP.get(c["name"], c["name"]), {})
        for key, provenance in c["inputs"].items():
            assert key in record, f"{c['code']} {key} is not from BENCHMARKS"
            assert provenance["value"] == record[key], f"{c['code']} {key}"


def test_a_country_with_no_data_has_no_inputs_and_no_default(fake):
    result = score_country("Nowhere")

    assert result["inputs"] == {}
    assert set(result["missing_inputs"]) == set(REQUIRED_INPUTS)
    assert result["esg"] is None
    assert all(result[k] is None for k in ("hdi", "gini_index", "gov_effectiveness"))


def test_reference_profile_is_separate_and_never_the_score():
    row = _client().get("/map/countries/ISL").json()

    assert "esg_score" not in row
    assert row["reference_profile"]["esg"] == 86
    assert row["esg"] is None


def test_physical_fields_are_measured_only(real_cache):
    _put_live(real_cache, "Germany", age_hours=0,
              indicators={"renewable_share": 45.0, "co2_per_capita": 6.9},
              sources={"renewable_share": "world_bank", "co2_per_capita": "benchmark"})

    result = score_country("Germany")

    assert result["renewable_share_pct"] == 45.0
    assert result["co2_intensity_t_per_capita"] is None  # benchmark-only is never a measurement


def test_the_finite_helper_refuses_bool_and_non_finite():
    assert map_data._finite(True) is None
    assert map_data._finite(float("nan")) is None
    assert map_data._finite(0) == 0.0
    assert math.isfinite(map_data._finite(3))
