"""The map API sends no invented values and no mislabelled sources.

Defects this file pins:

1. Invented values. /map/countries and /map/countries/{code} derived
   renewable_share_pct and co2_intensity_t_per_capita from the ESG score when
   nothing was measured: 0.95 * ESG and 20 - 0.18 * ESG.
2. Mislabelled source. A score computed from BENCHMARKS, because the live cache
   was empty or stale, was labelled "world_bank_live". Under C8 part 2 no value
   comes from BENCHMARKS or the live cache: every input is the pinned table's.
3. Empty sources. /map/russia sent sources_used = [] for every region, although
   the observations behind each score name the sources.
"""
import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.database as database
import app.external_data as ed
from app import map_inputs
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
def shipped(monkeypatch):
    """The process's real table, with the clock on the day it was written."""
    monkeypatch.setattr(map_inputs, "use_date", lambda: date(2026, 10, 19))
    map_inputs.reset_table_cache()
    yield
    map_inputs.reset_table_cache()


@pytest.fixture
def cache(monkeypatch):
    """A live cache the test fills. Under C8 part 2 it must not change any score."""
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


def test_physical_fields_are_the_pinned_values_never_derived_from_the_score(shipped):
    """Renewables and CO2 are the table's values, or null. The old formulas, 0.95 * ESG and 20 - 0.18 * ESG, are gone."""
    body = _client().get("/map/countries").json()
    rows = map_inputs.current_table_state().table.rows

    for c in body["countries"]:
        table_row = rows[c["code"]]["inputs"]
        expected_renew = table_row["renewable_share"]["value"]
        expected_co2 = table_row["co2_per_capita"]["value"]
        assert c["renewable_share_pct"] == round(expected_renew, 1), c["code"]
        assert c["co2_intensity_t_per_capita"] == round(expected_co2, 1), c["code"]
        if c["esg"] is not None:
            assert c["renewable_share_pct"] != min(round(c["esg"] * 0.95, 1), 95.0), c["code"]
            assert c["co2_intensity_t_per_capita"] != round(20.0 - c["esg"] * 0.18, 2), c["code"]


def test_country_detail_sends_no_derived_values(shipped):
    """The detail route agrees with the list, and its profile field is the reference, never the score."""
    row = _client().get("/map/countries/ISL").json()
    listed = _countries(_client())["ISL"]

    assert row["esg"] == listed["esg"]
    assert row["esg_status"] == listed["esg_status"]
    assert row["esg_source"] == listed["esg_source"]
    assert row["reference_profile"]["esg"] == 86  # ISL's hand-entered COUNTRIES value, unscored
    assert row["renewable_share_pct"] == listed["renewable_share_pct"]
    assert row["co2_intensity_t_per_capita"] == listed["co2_intensity_t_per_capita"]
    assert row["csrd_alignment"] is None
    assert row["audit_ready_share_pct"] is None


def test_no_score_is_labelled_from_a_source_other_than_the_table(shipped, cache):
    """A fresh live entry for every country changes nothing: no label, input or number moves."""
    before = _countries(_client())
    for c in map_data.COUNTRIES:
        _put_live(cache, c["name"], age_hours=0,
                  indicators={"renewable_share": 99.0, "gini_index": 99.0, "gov_effectiveness": 9.0},
                  sources={"renewable_share": "world_bank", "gini_index": "world_bank", "gov_effectiveness": "world_bank"})

    after = _countries(_client())

    for code in before:
        assert after[code]["esg"] == before[code]["esg"], code
        assert after[code]["esg_source"] == before[code]["esg_source"], code
        assert after[code]["inputs"] == before[code]["inputs"], code
        assert "world_bank_live" not in str(after[code]), code


def test_a_country_on_the_table_is_labelled_pinned_and_says_so(shipped):
    row = _countries(_client())["DEU"]

    assert row["esg_status"] == "ok"
    assert row["esg_source"] == "pinned_table"
    assert row["inputs"]["renewable_share"]["source"].startswith("World Bank EG.FEC.RNEW.ZS")
    assert row["inputs"]["renewable_share"]["fetched_at"] is not None


def test_esg_source_values_are_described_in_the_response(shipped):
    body = _client().get("/map/countries").json()

    meanings = body["esg_source_meanings"]
    assert set(meanings) == {"pinned_table"}
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
