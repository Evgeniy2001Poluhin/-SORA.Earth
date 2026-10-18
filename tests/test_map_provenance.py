"""The map API sends no invented values and no mislabelled sources.

Three defects, each pinned here:

1. Invented values. /map/countries and /map/countries/{code} derived
   renewable_share_pct and co2_intensity_t_per_capita from the ESG score when
   nothing was measured: 0.95 * ESG and 20 - 0.18 * ESG.
2. Mislabelled source. A score computed from BENCHMARKS, because the live cache
   was empty or stale, was labelled "world_bank_live".
3. Empty sources. /map/russia sent sources_used = [] for every region, although
   the observations behind each score name the sources.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.database as database
import app.external_data as ed
from app.api import map_data
from app.database import EnvironmentalObservation, IngesterRun, RegionESGScore, RegionSignal
from app.ingesters import persist
from app.ingesters.runner import run_all_ingesters
from app.routes import map_russia
from app.services import esg_aggregator
from tests.test_esg_aggregator_reads_the_live_source import _create_for_sqlite


# ---------- /map/countries ----------

def _client() -> TestClient:
    """A FastAPI app with the same router app.main mounts for /map/countries."""
    api = FastAPI()
    api.include_router(map_data.router)
    return TestClient(api)


def _countries(client) -> dict:
    return {c["code"]: c for c in client.get("/map/countries").json()["countries"]}


@pytest.fixture
def cache(monkeypatch):
    """An empty live cache for one test. monkeypatch restores the module's own dicts."""
    live, stamps = {}, {}
    monkeypatch.setattr(ed, "_live_cache", live)
    monkeypatch.setattr(ed, "_cache_timestamps", stamps)
    return live, stamps


def _put_live(cache, name, *, age_hours, indicators, sources):
    """Store a live-cache entry the way get_country_esg_realtime does, aged by age_hours."""
    live, stamps = cache
    live[name] = {
        "country": name,
        "indicators": indicators,
        "indicator_sources": sources,
        "indicator_periods": {},
    }
    stamps[name] = datetime.now() - timedelta(hours=age_hours)


def test_unmeasured_physical_fields_are_null_not_derived(cache):
    """With no live measurement, renewables and CO2 are null for every country.

    ISL and NZL have no BENCHMARKS row, so they take the hardcoded path, which used
    to send 0.95 * ESG and 20 - 0.18 * ESG. With the cache empty, every other
    country takes the static path, where CO2 was a BENCHMARKS figure sent as live.
    """
    body = _client().get("/map/countries").json()

    for c in body["countries"]:
        assert c["renewable_share_pct"] is None, f"{c['code']} renewables {c['renewable_share_pct']}"
        assert c["co2_intensity_t_per_capita"] is None, f"{c['code']} co2 {c['co2_intensity_t_per_capita']}"
        # The old formulas, at this country's own score, are absent. With no score
        # (C8: incomplete) there is no score to derive from, so the check is vacuous there.
        if c["esg"] is not None:
            assert c["renewable_share_pct"] != min(round(c["esg"] * 0.95, 1), 95.0), c["code"]
            assert c["co2_intensity_t_per_capita"] != round(20.0 - c["esg"] * 0.18, 2), c["code"]

    rows = {c["code"]: c for c in body["countries"]}
    # ISL has no BENCHMARKS row. C8: no input qualifies, so there is no score and no source.
    assert rows["ISL"]["esg_status"] == "incomplete"
    assert rows["ISL"]["esg"] is None
    assert rows["ISL"]["esg_source"] is None


def test_country_detail_sends_no_derived_values():
    """The detail route sent the same two formulas, plus two more derived from the score."""
    row = _client().get("/map/countries/ISL").json()

    # C8: the COUNTRIES profile never produces a score; it is a separate reference field.
    assert row["esg"] is None
    assert row["esg_status"] == "incomplete"
    assert row["esg_source"] is None
    assert row["reference_profile"]["esg"] == 86  # ISL's hand-entered COUNTRIES value, unscored
    assert row["renewable_share_pct"] is None
    assert row["co2_intensity_t_per_capita"] is None
    assert row["csrd_alignment"] is None
    assert row["audit_ready_share_pct"] is None


def test_benchmark_fallback_is_not_labelled_live_when_cache_is_empty_or_stale(cache):
    """BENCHMARKS never reads as live. C8: gini and gov have no stated source, so DEU is incomplete."""
    from app.api.map_data import score_country

    client = _client()

    # Cache empty: renewables and hdi are static with a stated source; gini and gov are not.
    row = _countries(client)["DEU"]
    assert row["esg_source"] is None
    assert row["esg_status"] == "incomplete"
    assert row["esg"] is None
    assert row["renewable_share_pct"] is None
    assert "gini_index" in row["missing_inputs"] and "gov_effectiveness" in row["missing_inputs"]
    assert row["inputs"]["renewable_share"]["kind"] == "static"
    assert row["inputs"]["renewable_share"]["source"] == "World Bank"
    assert row["inputs"]["renewable_share"]["fetched_at"] is None
    assert score_country("Germany")["esg"] is None

    # Cache stale: 48 hours old against a 24-hour TTL. Identical to empty.
    _put_live(cache, "Germany", age_hours=48,
              indicators={"renewable_share": 45.0},
              sources={"renewable_share": "world_bank"})
    stale = _countries(client)["DEU"]
    assert stale["esg_status"] == "incomplete"
    assert stale["renewable_share_pct"] is None
    assert stale["inputs"]["renewable_share"]["kind"] == "static"


def test_fresh_live_cache_is_measured_where_it_measures(cache):
    """A fresh entry reports its measured renewables. Benchmark CO2 is never reported as measured.

    C8: gov_effectiveness is benchmark-only, so DEU stays incomplete, but its renewables
    are still a measurement and are shown as one.
    """
    _put_live(cache, "Germany", age_hours=0,
              indicators={"renewable_share": 45.0, "co2_per_capita": 6.9},
              sources={"renewable_share": "world_bank", "co2_per_capita": "benchmark"})

    row = _countries(_client())["DEU"]

    assert row["esg_status"] == "incomplete"
    assert row["esg_source"] is None
    assert row["renewable_share_pct"] == 45.0
    assert row["co2_intensity_t_per_capita"] is None
    assert row["inputs"]["renewable_share"]["source"] == "world_bank"
    assert row["inputs"]["renewable_share"]["kind"] == "measured"
    assert row["inputs"]["renewable_share"]["fetched_at"] is not None


def test_esg_source_values_are_described_in_the_response(cache):
    body = _client().get("/map/countries").json()

    meanings = body["esg_source_meanings"]
    assert set(meanings) == {"world_bank_live", "mixed"}
    assert all(text.strip() for text in meanings.values())
    assert {c["esg_source"] for c in body["countries"] if c["esg_source"] is not None} <= set(meanings)


# ---------- /map/russia: sources_used ----------

class _Record(dict):
    """A row that supports r["column"], as asyncpg records do."""


class _Conn:
    """Answers the two queries map_russia makes, from rows held in memory."""

    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, sql, *params):
        return [_Record(r) for r in self.rows]

    async def fetchrow(self, sql, *params):
        for r in self.rows:
            if r["region_code"] == params[0]:
                return _Record(r)
        return None


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class _Pool:
    def __init__(self, rows):
        self.conn = _Conn(rows)

    def acquire(self):
        return _Acquire(self.conn)


def _snapshot(code):
    """A regional_esg_snapshot row as the view returns it: sources_used is always []."""
    return {
        "region_code": code,
        "e_score": 87.0,
        "s_score": 88.0,
        "g_score": 90.0,
        "score": 89.0,
        "confidence": 0.67,
        "sources_used": [],
        "sources_missing": [],
        "computed_at": datetime.now(timezone.utc),
        "stale_since": None,
        "model_version": None,
        "features": None,
    }


@pytest.fixture
def pool_with(monkeypatch):
    def install(codes):
        pool = _Pool([_snapshot(c) for c in codes])

        async def get_pool():
            return pool

        monkeypatch.setattr(map_russia, "_get_pool", get_pool)
    return install


@pytest.fixture
def session_factory(tmp_path, monkeypatch):
    """Real observations in SQLite, written by the offline ingesters."""
    engine = create_engine(f"sqlite:///{tmp_path}/map_provenance.db")
    _create_for_sqlite(
        (EnvironmentalObservation, RegionESGScore, RegionSignal, IngesterRun), engine
    )
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    for mod in (database, persist, esg_aggregator):
        monkeypatch.setattr(mod, "SessionLocal", factory)
    asyncio.run(run_all_ingesters())
    return factory


def test_russia_list_sends_the_sources_behind_each_score(session_factory, pool_with):
    """RU-MOW has all six required inputs, from rosstat and sber_veb_baseline.

    RU-NOPE has no observations behind its snapshot row, so its sources are not known
    and the field stays empty.
    """
    pool_with(["RU-MOW", "RU-NOPE"])

    result = asyncio.run(map_russia.russia_regions())

    assert result["source"] == "db-esg-v1-asyncpg"
    by_code = {r["code"]: r for r in result["regions"]}
    assert by_code["RU-MOW"]["sources_used"] == ["rosstat", "sber_veb_baseline"]
    assert by_code["RU-NOPE"]["sources_used"] == []


def test_region_detail_sends_the_sources_behind_its_score(session_factory, pool_with):
    pool_with(["RU-MOW"])

    result = asyncio.run(map_russia.region_detail("RU-MOW"))

    assert result["sources_used"] == ["rosstat", "sber_veb_baseline"]
