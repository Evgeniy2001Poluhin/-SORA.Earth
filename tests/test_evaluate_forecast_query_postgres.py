"""The phase 6 report's query, executed on the one engine it runs on.

`scripts/evaluate_forecast.py` produces the phase 6 report. Its pipeline is
covered end to end by `tests/test_evaluate_forecast_script.py` -- with
`load_rows` replaced, because the query uses `now() at time zone 'utc'` and
`make_interval(days => ...)`, and SQLite has neither. So the one function that
talks to the database had never been executed by a test. The roadmap's exit
criterion for the phase is "one reproducible report over one snapshot", and the
part that reads the snapshot was the part nobody had run.

This runs it against a migrated scratch PostgreSQL database and checks:

1. the filters -- source, indicator, `temporal_kind = 'observed'`, no NULL
   value, the window -- including a `legacy_ingestion_time` row whose
   `event_time` sits inside the window, which only the kind filter excludes;
2. that the window does not depend on the session's `TimeZone`;
3. that a report built by `main()` over the snapshot counts what the query
   returned.

## The second check is the one that failed

`now() at time zone 'utc'` is a timestamp *without* a zone: the UTC wall clock.
`event_time` is `timestamptz`, so PostgreSQL converts the cutoff back to an
instant using the session's `TimeZone`. Under `Asia/Tokyo` the UTC wall clock
is read as Tokyo time and the window reaches nine hours further back; under
`America/Los_Angeles` it stops seven hours short. The rows in the report
depended on a connection setting, which is not a reproducible report.

`now() - make_interval(...)` is an instant minus an interval, and compares as
one. `tests/test_refresh_log_timestamps.py` pins the same property for the
`data_refresh_log` cutoff, for the same reason.

Latent today rather than live: production's sessions are UTC, and with the
default `--days 800` no row lies near the boundary yet. It becomes live with a
non-UTC session, or with the first run of `--days` short enough to cut through
the data.
"""
from __future__ import annotations

import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from tests.postgres_scratch import requires_postgres, scratch_db  # noqa: F401  (fixture)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "evaluate_forecast.py")

pytestmark = requires_postgres


@pytest.fixture()
def script():
    spec = importlib.util.spec_from_file_location("evaluate_forecast_pg", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _insert(engine, rows):
    with engine.begin() as conn:
        for row in rows:
            values = {
                "value": None, "event_time": None, "period_start": None,
                "period_end": None, "source_revision": None, **row,
            }
            conn.execute(text(
                "INSERT INTO environmental_observations "
                "(region_id, indicator, value, source, event_time, period_start,"
                " period_end, source_revision, temporal_kind) "
                "VALUES (:region_id, :indicator, :value, :source, :event_time,"
                " :period_start, :period_end, :source_revision, :temporal_kind)"
            ), values)


def _session_in(monkeypatch, url, session_timezone):
    """Point `load_rows` at the scratch database, in a chosen TimeZone."""
    import app.database as database

    engine = create_engine(
        url, connect_args={"options": "-c TimeZone=%s" % session_timezone})
    monkeypatch.setattr(
        database, "SessionLocal",
        sessionmaker(bind=engine, autocommit=False, autoflush=False))
    return engine


def test_the_query_applies_every_filter(script, scratch_db, monkeypatch):
    engine, url = scratch_db
    now = datetime.now(timezone.utc)
    point = script.declared_points()[0]
    inside = now - timedelta(days=1)
    base = {"region_id": point, "source": "openmeteo", "indicator": "temperature"}

    _insert(engine, [
        {**base, "value": 10.0, "event_time": inside, "temporal_kind": "observed"},
        # Each of these differs from the row above in exactly one respect.
        {**base, "value": 11.0, "event_time": inside,
         "temporal_kind": "legacy_ingestion_time"},
        {**base, "indicator": "humidity", "value": 12.0, "event_time": inside,
         "temporal_kind": "observed"},
        {**base, "source": "openaq", "value": 13.0, "event_time": inside,
         "temporal_kind": "observed"},
        {**base, "value": None, "event_time": inside, "temporal_kind": "observed"},
        {**base, "value": 15.0, "event_time": now - timedelta(days=30),
         "temporal_kind": "observed"},
        {**base, "value": 16.0, "temporal_kind": "period", "source_revision": "r1",
         "period_start": now - timedelta(days=2), "period_end": now},
    ])
    session_engine = _session_in(monkeypatch, url, "UTC")
    try:
        rows = script.load_rows("openmeteo", "temperature", 7)
    finally:
        session_engine.dispose()

    assert [(r[0], r[2]) for r in rows] == [(point, 10.0)], (
        f"expected only the observed openmeteo temperature inside the window; "
        f"got {rows}"
    )
    assert isinstance(rows[0][2], float)
    assert abs((rows[0][1] - inside).total_seconds()) < 1, rows[0][1]


@pytest.mark.parametrize(
    "session_timezone",
    ["UTC", "Europe/Moscow", "Asia/Tokyo", "America/Los_Angeles"],
)
def test_the_window_is_the_same_in_every_session_timezone(
        script, scratch_db, monkeypatch, session_timezone):
    engine, url = scratch_db
    now = datetime.now(timezone.utc)
    point = script.declared_points()[0]
    cutoff = now - timedelta(days=1)
    base = {"region_id": point, "source": "openmeteo", "indicator": "temperature",
            "temporal_kind": "observed"}

    # One hour either side of the one-day cutoff: far wider than the time
    # between Python's clock and the database's, far narrower than any zone
    # offset under test.
    _insert(engine, [
        {**base, "value": 1.0, "event_time": cutoff + timedelta(hours=1)},
        {**base, "value": 2.0, "event_time": cutoff - timedelta(hours=1)},
    ])
    session_engine = _session_in(monkeypatch, url, session_timezone)
    try:
        values = [r[2] for r in script.load_rows("openmeteo", "temperature", 1)]
    finally:
        session_engine.dispose()

    assert values == [1.0], (
        f"with the session in {session_timezone} the one-day window returned "
        f"{values}; the row an hour inside it is 1.0 and the row an hour "
        f"outside it is 2.0. The cutoff moved with the session's TimeZone."
    )


def test_a_report_over_the_snapshot_counts_what_the_query_returned(
        script, scratch_db, monkeypatch, capsys):
    """`main()` end to end on PostgreSQL: the report the phase exits on.

    `snapshot.observations` counts *daily* observations -- point-days with at
    least `required_hours()` distinct UTC hours -- not rows. So the snapshot is
    one complete UTC day of one point: 24 rows in, one daily observation out.
    """
    engine, url = scratch_db
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    point = script.declared_points()[0]
    _insert(engine, [
        {"region_id": point, "source": "openmeteo", "indicator": "temperature",
         "temporal_kind": "observed", "value": 10.0 + h / 10,
         "event_time": yesterday + timedelta(hours=h)}
        for h in range(24)
    ])
    session_engine = _session_in(monkeypatch, url, "UTC")
    try:
        assert script.main(["--days", "7"]) == 0
    finally:
        session_engine.dispose()

    snapshot = json.loads(capsys.readouterr().out)["snapshot"]
    assert snapshot["point_days_examined"] == 1, snapshot
    assert snapshot["point_days_below_floor"] == 0, snapshot
    assert snapshot["observations"] == 1, snapshot
    assert snapshot["first_day"] == snapshot["last_day"], snapshot
    assert snapshot["declared_points"] == 21
    assert snapshot["declared_points_source"] == "caller"
