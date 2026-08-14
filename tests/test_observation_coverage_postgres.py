"""A day that fell short is visible, because it moves the M3 date.

The M3 declaration binds the target to a daily mean computed only where at
least 80% of the expected hourly observations are present. A day below that is
absent, and an absent day is one fewer window the §7 gate can use -- so a quiet
dip in coverage moves the earliest evidential date.

Nothing watched for it. `/ingestion/attention` reports the verdict of each
source's latest *run*: it catches "the ingester stopped", and weakly, since
openmeteo declares no `max_vintage_hours`. A day where 18 of 24 observations
arrived leaves every run successful and every freshness check content.

Against PostgreSQL, because the query is PostgreSQL's: `make_interval`,
`at time zone`, and a date cast in the GROUP BY.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from tests.postgres_scratch import (  # noqa: F401  (scratch_db is a fixture)
    requires_postgres,
    scratch_db,
)

pytestmark = requires_postgres

INSERT = text(
    "INSERT INTO environmental_observations "
    "  (region_id, indicator, value, source, source_record_id, temporal_kind, "
    "   event_time, ingested_at, created_at, updated_at, is_valid) "
    "VALUES (:region, 'temperature', 1.0, 'openmeteo', :rid, 'observed', "
    "        :event_time, now(), now(), now(), true)"
)


def _fill(engine, day_offset, region, count):
    """`count` observations for one point on one past UTC day."""
    base = (datetime.now(timezone.utc) - timedelta(days=day_offset)
            ).replace(hour=0, minute=0, second=0, microsecond=0)
    with engine.begin() as conn:
        for hour in range(count):
            conn.execute(INSERT, {
                "region": region,
                "rid": f"{region}-{day_offset}-{hour}",
                "event_time": base + timedelta(hours=hour),
            })


def _coverage(engine, **params):
    import app.api.infra as infra
    import app.database as database
    from sqlalchemy.orm import sessionmaker

    original = database.SessionLocal
    database.SessionLocal = sessionmaker(bind=engine)
    try:
        return infra.observation_coverage(_admin=None, **params)
    finally:
        database.SessionLocal = original


@requires_postgres
def test_the_threshold_is_the_gate_constant(scratch_db):
    """Nineteen of twenty-four, from MIN_COVERAGE -- not a second number."""
    engine, _ = scratch_db
    from app.services.forecasting.entry_conditions import MIN_COVERAGE

    result = _coverage(engine, source="openmeteo", indicator="temperature", days=30)

    assert result.required_per_day == int(24 * MIN_COVERAGE) == 19


@requires_postgres
def test_a_short_day_is_reported(scratch_db):
    engine, _ = scratch_db
    _fill(engine, 2, "RU-MOW", 18)

    result = _coverage(engine, source="openmeteo", indicator="temperature", days=30)

    assert result.gap_count == 1
    assert result.gaps[0].observations == 18
    assert result.gaps[0].region_id == "RU-MOW"


@requires_postgres
def test_a_full_day_is_not(scratch_db):
    """Otherwise the report is satisfied by one that flags everything."""
    engine, _ = scratch_db
    _fill(engine, 2, "RU-MOW", 23)

    result = _coverage(engine, source="openmeteo", indicator="temperature", days=30)

    assert result.gap_count == 0
    assert result.complete_days == 1


@requires_postgres
def test_the_denominator_travels_with_the_answer(scratch_db):
    """An empty gap list means "nothing fell short" or "nothing was looked at",
    and those are different facts about a deployment."""
    engine, _ = scratch_db

    result = _coverage(engine, source="openmeteo", indicator="temperature", days=30)

    assert result.gap_count == 0
    assert result.days_examined == 0
    assert result.points_examined == 0


@requires_postgres
def test_today_is_excluded(scratch_db):
    """Incomplete by construction. Reporting it would put one guaranteed gap in
    every response, and an alert that fires daily is one nobody reads."""
    engine, _ = scratch_db
    _fill(engine, 0, "RU-MOW", 3)

    result = _coverage(engine, source="openmeteo", indicator="temperature", days=30)

    assert result.gap_count == 0
    assert result.days_examined == 0


@requires_postgres
def test_the_window_is_honoured(scratch_db):
    engine, _ = scratch_db
    _fill(engine, 2, "RU-MOW", 18)
    _fill(engine, 40, "RU-SPE", 18)

    result = _coverage(engine, source="openmeteo", indicator="temperature", days=7)

    assert {g.region_id for g in result.gaps} == {"RU-MOW"}


@requires_postgres
def test_another_source_is_not_counted(scratch_db):
    engine, _ = scratch_db
    _fill(engine, 2, "RU-MOW", 18)

    result = _coverage(engine, source="openaq", indicator="temperature", days=30)

    assert result.gap_count == 0
    assert result.days_examined == 0
