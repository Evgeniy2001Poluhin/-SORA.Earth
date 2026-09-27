"""
Test that app/routes/map_russia.py emits stale_since in all read paths.

The backend writes stale_since and stale_reason to region_esg_scores when a
region's inputs stop being fresh (app/services/esg_aggregator.py _mark_stale),
but the view regional_esg_snapshot did not expose them until the migration
e8f9a1b2c3d4. This test verifies that every read path in map_russia.py now
emits stale_since.
"""
import pytest
import asyncio
import re
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal, RegionESGScore
from app.routes import map_russia

client = TestClient(app)


def test_sqlalchemy_fallback_emits_stale_since_for_stale_region():
    """
    The SQLAlchemy fallback path on SQLite emits stale_since for a stale
    RegionESGScore and null for a fresh one.
    """
    db = SessionLocal()
    try:
        # Clear any existing rows
        db.query(RegionESGScore).delete()
        db.commit()

        # Create a stale region
        stale_date = datetime.now(timezone.utc) - timedelta(days=7)
        # On SQLite, IDENTITY doesn't work, so we provide id explicitly
        stale_region = RegionESGScore(
            id=1,
            region_code="RU-TEST-STALE",
            env_score=70.0,
            social_score=75.0,
            gov_score=80.0,
            total_score=75.0,
            confidence=0.67,
            sources_count=2,
            signals_used=10,
            stale_since=stale_date,
            stale_reason="test stale reason",
            updated_at=datetime.now(timezone.utc) - timedelta(days=8),
        )

        # Create a fresh region
        fresh_region = RegionESGScore(
            id=2,
            region_code="RU-TEST-FRESH",
            env_score=70.0,
            social_score=75.0,
            gov_score=80.0,
            total_score=75.0,
            confidence=0.67,
            sources_count=2,
            signals_used=10,
            stale_since=None,
            stale_reason=None,
            updated_at=datetime.now(timezone.utc),
        )

        db.add(stale_region)
        db.add(fresh_region)
        db.commit()

        # Call the endpoint - it will use the SQLAlchemy fallback on SQLite
        response = client.get("/api/v1/map/russia")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        data = response.json()
        assert "regions" in data, f"Expected 'regions' in response: {data}"
        regions = data["regions"]

        # Find our test regions
        stale = next((r for r in regions if r["code"] == "RU-TEST-STALE"), None)
        fresh = next((r for r in regions if r["code"] == "RU-TEST-FRESH"), None)

        assert stale is not None, f"Expected to find RU-TEST-STALE in {[r['code'] for r in regions]}"
        assert fresh is not None, f"Expected to find RU-TEST-FRESH in {[r['code'] for r in regions]}"

        # Verify stale_since is emitted correctly
        assert "stale_since" in stale, f"Expected 'stale_since' in stale region: {stale.keys()}"
        assert stale["stale_since"] is not None, "Expected stale_since to be set for stale region"
        # Verify it's a valid ISO timestamp
        parsed = datetime.fromisoformat(stale["stale_since"].replace("Z", "+00:00"))
        assert parsed.date() == stale_date.date(), f"Expected {stale_date.date()}, got {parsed.date()}"

        assert "stale_since" in fresh, f"Expected 'stale_since' in fresh region: {fresh.keys()}"
        assert fresh["stale_since"] is None, f"Expected stale_since to be null for fresh region, got {fresh['stale_since']}"

    finally:
        db.query(RegionESGScore).filter(
            RegionESGScore.region_code.in_(["RU-TEST-STALE", "RU-TEST-FRESH"])
        ).delete()
        db.commit()
        db.close()


# ========== Fake asyncpg pool for testing the asyncpg code paths ==========


class FakeAsyncpgRecord(dict):
    """A fake asyncpg.Record that supports r["col"] dict access."""
    pass


class FakeAsyncpgConnection:
    """Fake asyncpg connection that parses SELECT queries and returns matching records."""

    def __init__(self, backing_rows):
        self.backing_rows = backing_rows

    def _parse_select_columns(self, sql):
        """Extract column names from a SELECT statement.

        Returns list of (output_name, source_name) tuples.
        """
        # Find the part between SELECT and FROM
        select_match = re.search(r'SELECT\s+(.*?)\s+FROM', sql, re.IGNORECASE | re.DOTALL)
        if not select_match:
            return []

        columns_text = select_match.group(1)
        # Split on top-level commas (ignoring commas inside function calls)
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

        # Extract output names
        columns = []
        for part in parts:
            # Check for "AS alias"
            as_match = re.search(r'\s+AS\s+(\w+)', part, re.IGNORECASE)
            if as_match:
                output_name = as_match.group(1)
                source_name = part[:as_match.start()].strip()
            else:
                # No alias - use the column name itself
                # Remove any casts like ::text[]
                clean = re.sub(r'::\w+(\[\])?', '', part).strip()
                # Handle ARRAY[]::text[] AS sources_missing -> sources_missing
                if clean.startswith('ARRAY'):
                    # This is a constructed array, not a table column
                    output_name = part.split()[-1] if ' ' in part else clean
                    source_name = None
                elif clean.upper().startswith('NULL'):
                    # This is NULL::type - extract the name after AS or use 'NULL'
                    output_name = part.split()[-1] if ' ' in part else 'null_col'
                    source_name = None
                else:
                    output_name = clean
                    source_name = clean
            columns.append((output_name, source_name))

        return columns

    def _get_from_table(self, sql):
        """Extract the table name from the FROM clause."""
        from_match = re.search(r'FROM\s+(\w+)', sql, re.IGNORECASE)
        if from_match:
            return from_match.group(1)
        return None
    
    def _filter_rows(self, sql, args):
        """Filter backing rows based on WHERE clause."""
        # Check for WHERE region_code = $1
        where_match = re.search(r'WHERE\s+region_code\s*=\s*\$1', sql, re.IGNORECASE)
        if where_match and args:
            return [r for r in self.backing_rows if r.get("region_code") == args[0]]
        return self.backing_rows

    def _build_record(self, backing_row, columns):
        """Build a record with only the selected columns."""
        record = FakeAsyncpgRecord()
        for output_name, source_name in columns:
            if source_name is None:
                # Constructed value (ARRAY[], NULL::type, etc.)
                if output_name == "sources_missing":
                    record[output_name] = []
                elif "NULL" in output_name.upper() or output_name.startswith("null_"):
                    record[output_name] = None
                else:
                    record[output_name] = None
            elif source_name in backing_row:
                record[output_name] = backing_row[source_name]
            else:
                record[output_name] = None
        return record

    async def fetch(self, sql, *args):
        """Execute a query and return multiple rows."""
        # Return empty list for region_signals queries
        if self._get_from_table(sql) == "region_signals":
            return []
        columns = self._parse_select_columns(sql)
        filtered = self._filter_rows(sql, args)
        return [self._build_record(row, columns) for row in filtered]

    async def fetchrow(self, sql, *args):
        """Execute a query and return a single row."""
        # Return None for region_signals queries
        if self._get_from_table(sql) == "region_signals":
            return None
        rows = await self.fetch(sql, *args)
        return rows[0] if rows else None


class FakeAsyncpgPool:
    """Fake asyncpg pool for testing."""

    def __init__(self, backing_rows):
        self.backing_rows = backing_rows

    def acquire(self):
        """Return a context manager that yields a connection."""
        return FakeAsyncpgAcquireContext(self.backing_rows)


class FakeAsyncpgAcquireContext:
    """Async context manager for pool.acquire()."""

    def __init__(self, backing_rows):
        self.backing_rows = backing_rows

    async def __aenter__(self):
        return FakeAsyncpgConnection(self.backing_rows)

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


# ========== Tests using the fake asyncpg pool ==========


def test_asyncpg_load_from_db_emits_stale_since(monkeypatch):
    """
    The asyncpg _load_from_db() function emits stale_since correctly for both
    stale and fresh regions.
    """
    stale_date = datetime.now(timezone.utc) - timedelta(days=7)

    backing_rows = [
        {
            "region_code": "RU-STALE",
            "e_score": 70.0,
            "s_score": 75.0,
            "g_score": 80.0,
            "score": 75.0,
            "confidence": 0.67,
            "sources_used": ["rosstat", "green"],
            "computed_at": datetime.now(timezone.utc) - timedelta(days=8),
            "stale_since": stale_date,
        },
        {
            "region_code": "RU-FRESH2",
            "e_score": 72.0,
            "s_score": 76.0,
            "g_score": 81.0,
            "score": 76.0,
            "confidence": 0.71,
            "sources_used": ["rosstat", "green"],
            "computed_at": datetime.now(timezone.utc),
            "stale_since": None,
        },
    ]

    
    pool = FakeAsyncpgPool(backing_rows)
    async def get_pool():
        return pool
    monkeypatch.setattr(map_russia, "_get_pool", get_pool)
    
    async def scenario():
        # Test _load_from_db
        regions = await map_russia._load_from_db()

        assert len(regions) > 0, "Expected _load_from_db to return non-empty list"

        stale = next((r for r in regions if r["code"] == "RU-STALE"), None)
        fresh = next((r for r in regions if r["code"] == "RU-FRESH2"), None)

        assert stale is not None, f"Expected to find RU-STALE in {[r['code'] for r in regions]}"
        assert fresh is not None, f"Expected to find RU-FRESH2 in {[r['code'] for r in regions]}"

        # Verify stale region
        assert stale["stale_since"] is not None, "Expected stale_since to be set for stale region"
        assert stale["stale_since"] == stale_date.isoformat(), (
            f"Expected {stale_date.isoformat()}, got {stale['stale_since']}"
        )

        # Verify fresh region
        assert fresh["stale_since"] is None, (
            f"Expected stale_since to be None for fresh region, got {fresh['stale_since']}"
        )

    asyncio.run(scenario())


def test_asyncpg_russia_regions_emits_source_and_stale_since(monkeypatch):
    """
    The asyncpg path through russia_regions() emits source="db-esg-v1-asyncpg"
    and stale_since correctly.
    """
    stale_date = datetime.now(timezone.utc) - timedelta(days=7)

    backing_rows = [
        {
            "region_code": "RU-STALE",
            "e_score": 70.0,
            "s_score": 75.0,
            "g_score": 80.0,
            "score": 75.0,
            "confidence": 0.67,
            "sources_used": ["rosstat"],
            "computed_at": datetime.now(timezone.utc) - timedelta(days=8),
            "stale_since": stale_date,
        },
        {
            "region_code": "RU-FRESH2",
            "e_score": 72.0,
            "s_score": 76.0,
            "g_score": 81.0,
            "score": 76.0,
            "confidence": 0.71,
            "sources_used": ["rosstat"],
            "computed_at": datetime.now(timezone.utc),
            "stale_since": None,
        },
    ]

    
    pool = FakeAsyncpgPool(backing_rows)
    async def get_pool():
        return pool
    monkeypatch.setattr(map_russia, "_get_pool", get_pool)
    
    async def scenario():
        result = await map_russia.russia_regions()

        # Verify the asyncpg source
        assert result["source"] == "db-esg-v1-asyncpg", (
            f"Expected source='db-esg-v1-asyncpg', got {result['source']}"
        )

        # Verify we got both regions
        codes = {r["code"] for r in result["regions"]}
        assert codes == {"RU-STALE", "RU-FRESH2"}, f"Expected both regions, got {codes}"

        # Verify stale_since values
        regions = result["regions"]
        stale = next((r for r in regions if r["code"] == "RU-STALE"), None)
        fresh = next((r for r in regions if r["code"] == "RU-FRESH2"), None)

        assert stale["stale_since"] is not None
        parsed = datetime.fromisoformat(stale["stale_since"])
        assert abs((parsed - stale_date).total_seconds()) < 1

        assert fresh["stale_since"] is None

    asyncio.run(scenario())


def test_asyncpg_region_detail_emits_stale_since(monkeypatch):
    """
    The asyncpg region_detail() emits stale_since correctly for both stale
    and fresh regions, with no "error" key.
    """
    stale_date = datetime.now(timezone.utc) - timedelta(days=7)

    backing_rows = [
        {
            "region_code": "RU-STALE",
            "e_score": 70.0,
            "s_score": 75.0,
            "g_score": 80.0,
            "score": 75.0,
            "confidence": 0.67,
            "sources_used": ["rosstat"],
            "sources_missing": [],
            "computed_at": datetime.now(timezone.utc) - timedelta(days=8),
            "model_version": None,
            "features": None,
            "stale_since": stale_date,
        },
        {
            "region_code": "RU-FRESH2",
            "e_score": 72.0,
            "s_score": 76.0,
            "g_score": 81.0,
            "score": 76.0,
            "confidence": 0.71,
            "sources_used": ["rosstat"],
            "sources_missing": [],
            "computed_at": datetime.now(timezone.utc),
            "model_version": None,
            "features": None,
            "stale_since": None,
        },
    ]

    
    pool = FakeAsyncpgPool(backing_rows)
    async def get_pool():
        return pool
    monkeypatch.setattr(map_russia, "_get_pool", get_pool)
    
    async def scenario():
        # Test stale region
        stale_result = await map_russia.region_detail("RU-STALE")
        assert "error" not in stale_result, f"Expected no error key, got {stale_result.get('error')}"
        assert stale_result["stale_since"] is not None
        parsed = datetime.fromisoformat(stale_result["stale_since"])
        assert abs((parsed - stale_date).total_seconds()) < 1, (
            f"Expected {stale_date}, got {parsed}"
        )

        # Test fresh region
        fresh_result = await map_russia.region_detail("RU-FRESH2")
        assert "error" not in fresh_result, f"Expected no error key, got {fresh_result.get('error')}"
        assert fresh_result["stale_since"] is None, (
            f"Expected stale_since to be None for fresh region, got {fresh_result['stale_since']}"
        )

    asyncio.run(scenario())
