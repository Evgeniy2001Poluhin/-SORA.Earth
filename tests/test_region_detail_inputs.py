"""Test GET /api/v1/map/russia/{region_code} returns inputs instead of signals.

The route now returns the six inputs of the ESG score, fetched from
required_inputs_for_region, instead of reading region_signals (which is empty).
"""
import pytest
import asyncio
import re
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock

from app.routes import map_russia
from app.services import esg_aggregator
from app.ingesters import temporal
from app.database import SessionLocal, EnvironmentalObservation, RegionESGScore, RegionSignal, IngesterRun
from app.ingesters import persist
from app.ingesters.runner import run_all_ingesters
import app.database as database
from tests.test_esg_aggregator_reads_the_live_source import _create_for_sqlite
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


# ========== Fake asyncpg pool ==========


class FakeAsyncpgRecord(dict):
    """A fake asyncpg.Record that supports r["col"] dict access."""
    pass


class FakeAsyncpgConnection:
    """Fake asyncpg connection that parses SELECT queries and returns matching records."""

    def __init__(self, backing_rows):
        self.backing_rows = backing_rows

    def _parse_select_columns(self, sql):
        """Extract column names from a SELECT statement."""
        select_match = re.search(r'SELECT\s+(.*?)\s+FROM', sql, re.IGNORECASE | re.DOTALL)
        if not select_match:
            return []

        columns_text = select_match.group(1)
        parts = []
        current = ""
        paren_depth = 0
        for char in columns_text:
            if char == '(':
                paren_depth += 1
            elif char == ')':
                paren_depth -= 1
            elif char == ',' and paren_depth == 0:
                parts.append(current.strip())
                current = ""
                continue
            current += char
        if current.strip():
            parts.append(current.strip())

        columns = []
        for part in parts:
            as_match = re.search(r'\s+AS\s+(\w+)', part, re.IGNORECASE)
            if as_match:
                columns.append(as_match.group(1))
            else:
                columns.append(part.strip().split('.')[-1])
        return columns

    async def fetchrow(self, sql, *params):
        """Return one row matching the query."""
        columns = self._parse_select_columns(sql)
        if not columns:
            return None

        for row in self.backing_rows:
            rec = FakeAsyncpgRecord()
            for col in columns:
                rec[col] = row.get(col)
            return rec
        return None

    async def fetch(self, sql, *params):
        """Return all rows matching the query (empty for this test)."""
        return []


class FakeAsyncpgPool:
    """Fake asyncpg pool."""

    def __init__(self, backing_rows):
        self.backing_rows = backing_rows

    def acquire(self):
        return AsyncContextManager(FakeAsyncpgConnection(self.backing_rows))


class AsyncContextManager:
    def __init__(self, obj):
        self.obj = obj

    async def __aenter__(self):
        return self.obj

    async def __aexit__(self, *args):
        pass


# ========== Test fixtures ==========


@pytest.fixture
def session_factory(tmp_path, monkeypatch):
    """SQLite session factory for testing."""
    engine = create_engine(f"sqlite:///{tmp_path}/rd.db")
    _create_for_sqlite(
        (EnvironmentalObservation, RegionESGScore, RegionSignal, IngesterRun), engine
    )
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    for mod in (database, persist, esg_aggregator):
        monkeypatch.setattr(mod, "SessionLocal", factory)
    return factory


# ========== Tests ==========


def test_region_detail_returns_inputs(session_factory, monkeypatch):
    """region_detail returns 'inputs' with six entries from required_inputs_for_region.

    Fake asyncpg snapshot + real SQLite inputs from the ingester run. The response
    has 'inputs' with six entries (the ones from required_inputs_for_region), no
    'indicators', 'indicators_count', or 'signals_total'.
    """
    # Run ingesters to populate the database
    asyncio.run(run_all_ingesters())

    # Fake asyncpg snapshot for RU-MOW
    fake_snap_row = {
        "region_code": "RU-MOW",
        "e_score": 87.0,
        "s_score": 88.0,
        "g_score": 90.0,
        "score": 89.0,
        "confidence": 0.67,
        "sources_used": ["rosstat", "sber_veb_baseline"],
        "sources_missing": [],
        "computed_at": datetime.now(timezone.utc),
        "model_version": None,
        "features": None,
        "stale_since": None,
    }

    fake_pool = FakeAsyncpgPool([fake_snap_row])

    async def fake_get_pool():
        return fake_pool

    monkeypatch.setattr(map_russia, "_get_pool", fake_get_pool)

    # Call the endpoint
    result = asyncio.run(map_russia.region_detail("RU-MOW"))

    # Assertions
    assert "inputs" in result
    assert len(result["inputs"]) == 6

    # No old fields
    assert "indicators" not in result
    assert "indicators_count" not in result
    assert "signals_total" not in result

    # Inputs match required_inputs_for_region
    db = session_factory()
    expected = esg_aggregator.required_inputs_for_region(db, "RU-MOW")
    db.close()

    for i, inp in enumerate(result["inputs"]):
        exp = expected[i]
        assert inp["source"] == exp["source"]
        assert inp["indicator"] == exp["indicator"]
        assert inp["missing"] == exp["missing"]
        if not exp["missing"]:
            assert inp["value"] == exp["value"]


def test_region_detail_preserves_units(session_factory, monkeypatch):
    """Mutation M3: region_detail must NOT drop units from inputs.

    The route serializes inputs from required_inputs_for_region. A mutation that
    set every input's unit to None would pass the previous test (which checked only
    values). This test asserts each non-missing input's unit equals what
    required_inputs_for_region returns and is not None.
    """
    asyncio.run(run_all_ingesters())

    fake_snap_row = {
        "region_code": "RU-MOW",
        "e_score": 87.0,
        "s_score": 88.0,
        "g_score": 90.0,
        "score": 89.0,
        "confidence": 0.67,
        "sources_used": ["rosstat", "sber_veb_baseline"],
        "sources_missing": [],
        "computed_at": datetime.now(timezone.utc),
        "model_version": None,
        "features": None,
        "stale_since": None,
    }

    fake_pool = FakeAsyncpgPool([fake_snap_row])

    async def fake_get_pool():
        return fake_pool

    monkeypatch.setattr(map_russia, "_get_pool", fake_get_pool)

    result = asyncio.run(map_russia.region_detail("RU-MOW"))

    # Read expected inputs from the aggregator
    db = session_factory()
    expected = esg_aggregator.required_inputs_for_region(db, "RU-MOW")
    db.close()

    # Every non-missing input must have its unit preserved
    for i, inp in enumerate(result["inputs"]):
        exp = expected[i]
        if not exp["missing"]:
            assert inp["unit"] == exp["unit"], \
                f"{inp['source']}:{inp['indicator']}: expected unit {exp['unit']}, got {inp['unit']}"
            assert inp["unit"] is not None, \
                f"{inp['source']}:{inp['indicator']}: unit was dropped"


def _moscow_snapshot():
    return {
        "region_code": "RU-MOW",
        "e_score": 87.0,
        "s_score": 88.0,
        "g_score": 90.0,
        "score": 89.0,
        "confidence": 0.67,
        "sources_used": ["rosstat", "sber_veb_baseline"],
        "sources_missing": [],
        "computed_at": datetime.now(timezone.utc),
        "model_version": None,
        "features": None,
        "stale_since": None,
    }


def _serve_snapshots(monkeypatch, rows):
    pool = FakeAsyncpgPool(rows)

    async def fake_get_pool():
        return pool

    monkeypatch.setattr(map_russia, "_get_pool", fake_get_pool)


def _unreadable(db, region_id):
    raise RuntimeError("the inputs could not be read")


def test_a_failed_inputs_read_is_not_an_empty_list(session_factory, monkeypatch):
    """A failed read answers `inputs: None`, not `[]`.

    `[]` renders as a table with no rows -- indistinguishable from a region with
    no inputs -- and the failure would be visible only in the log.
    """
    _serve_snapshots(monkeypatch, [_moscow_snapshot()])
    monkeypatch.setattr(esg_aggregator, "required_inputs_for_region", _unreadable)

    result = asyncio.run(map_russia.region_detail("RU-MOW"))

    assert "error" not in result
    assert result["inputs"] is None
    # Control: the snapshot still reached the response.
    assert result["esg"]["score"] == 89.0


def test_a_failed_inputs_read_is_not_region_not_found(session_factory, monkeypatch):
    """Without a snapshot row, a failed read is not evidence the region is absent."""
    _serve_snapshots(monkeypatch, [])
    monkeypatch.setattr(esg_aggregator, "required_inputs_for_region", _unreadable)

    result = asyncio.run(map_russia.region_detail("RU-MOW"))

    assert result.get("error") != "region not found"
    assert result["inputs"] is None

    # Control: inputs read, every one missing, no snapshot -- that IS not found.
    def all_missing(db, region_id):
        return [
            {"source": src, "indicator": ind, "value": None, "unit": None,
             "temporal_kind": None, "period_start": None, "period_end": None,
             "delivered_at": None, "source_revision": None, "missing": True}
            for src, ind in esg_aggregator.REQUIRED_METRICS
        ]

    monkeypatch.setattr(esg_aggregator, "required_inputs_for_region", all_missing)
    assert asyncio.run(map_russia.region_detail("RU-MOW")) == {
        "error": "region not found", "region_code": "RU-MOW"}
