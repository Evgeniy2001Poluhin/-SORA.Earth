"""Observation coverage counts against the expected grid, on PostgreSQL.

A point-day is expected when the registry declares its point and the day is on
or after the point's `since`. Each expected point-day is matched against the
distinct UTC hours observed for it. A point-day with no rows at all is an
expected pair with zero hours, so it is a gap. Before the grid, GROUP BY saw only
the pairs that had rows, and a point silent for a whole day was not counted --
the defect pinned by the strict xfail this file replaced.

Against PostgreSQL, because the query is PostgreSQL's: `generate_series`,
`unnest` over arrays, `at time zone`, and `date_trunc`.

Every test states its calendar with TODAY, so the window does not depend on the
day the suite runs. Each test gets a freshly migrated database (scratch_db).
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from tests.postgres_scratch import (  # noqa: F401  (scratch_db is a fixture)
    requires_postgres,
    scratch_db,
)

pytestmark = requires_postgres

#: The 21 points of openmeteo-points-v1, in the registry's order. Written here as
#: a literal so this file states the declared set itself;
#: tests/test_observation_coverage_expected_grid.py holds it equal to the registry.
DECLARED = [
    "DEU", "GBR", "FRA", "ESP", "ITA", "NLD", "POL",
    "IND", "CHN", "JPN", "KOR", "IDN",
    "USA", "BRA", "MEX", "CAN",
    "NGA", "ZAF", "KEN",
    "RU-MOW", "RU-SPE",
]

TODAY = date(2026, 10, 20)

#: One row in every UTC hour of a day.
HOURLY = [h * 60 for h in range(24)]

#: Two rows in every UTC hour, at :10 and :40 -- 48 rows, 24 hours.
TWICE = [h * 60 + m for h in range(24) for m in (10, 40)]

INSERT = text(
    "INSERT INTO environmental_observations "
    "  (region_id, indicator, value, source, source_record_id, temporal_kind, "
    "   event_time, ingested_at, created_at, updated_at, is_valid) "
    "VALUES (:region, 'temperature', 1.0, 'openmeteo', :rid, 'observed', "
    "        :event_time, now(), now(), now(), true)"
)

ZONES = ["UTC", "Asia/Tokyo", "America/Los_Angeles", "Asia/Kolkata"]


def _day(back):
    """The UTC day `back` days before TODAY."""
    return TODAY - timedelta(days=back)


def _window_days(n, today=TODAY):
    """The n complete UTC days before today, oldest first."""
    return [today - timedelta(days=k) for k in range(n, 0, -1)]


def _rows(day, code, minutes, tag):
    base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return [{
        "region": code,
        "rid": f"{code}-{day.isoformat()}-{tag}-{index}",
        "event_time": base + timedelta(minutes=minute),
    } for index, minute in enumerate(minutes)]


def _insert(engine, rows):
    if rows:
        with engine.begin() as conn:
            conn.execute(INSERT, rows)


def _grid(engine, days, codes=DECLARED, minutes=HOURLY, overrides=None):
    """Every code on every day with `minutes`, except where overridden.

    `overrides` maps (day, code) to a list of minute offsets for that point-day,
    or to None for no rows at all. Anything not overridden gets HOURLY.
    """
    overrides = overrides or {}
    rows = []
    for day in days:
        for code in codes:
            chosen = overrides.get((day, code), minutes)
            if chosen is None:
                continue
            rows += _rows(day, code, chosen, "grid")
    _insert(engine, rows)


def _report(url, session_timezone=None, **params):
    """coverage_report against a database whose session is in one zone."""
    import app.database as database
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.services.observation_coverage import coverage_report

    connect_args = {}
    if session_timezone is not None:
        connect_args = {"options": "-c TimeZone=%s" % session_timezone}
    engine = create_engine(url, connect_args=connect_args)
    original = database.SessionLocal
    database.SessionLocal = sessionmaker(bind=engine)
    try:
        return coverage_report(**params)
    finally:
        database.SessionLocal = original
        engine.dispose()


def _gap_keys(result):
    return [(g.day, g.region_id, g.observations) for g in result.gaps]


def _counts(result):
    return (result.days_examined, result.points_examined,
            result.complete_days, result.gap_count)


# ---- the threshold and the basic grid -------------------------------------

@requires_postgres
def test_the_threshold_is_the_gate_constant(scratch_db):
    """Nineteen of twenty-four, from MIN_COVERAGE -- not a second number."""
    from app.services.forecasting.entry_conditions import MIN_COVERAGE

    _, url = scratch_db
    result = _report(url, source="openmeteo", indicator="temperature",
                     days=7, today=TODAY)

    assert result.required_per_day == int(24 * MIN_COVERAGE) == 19


@requires_postgres
def test_a_short_day_is_reported(scratch_db):
    engine, url = scratch_db
    _grid(engine, _window_days(7), overrides={
        (_day(2), "RU-MOW"): [h * 60 for h in range(18)],
    })

    result = _report(url, source="openmeteo", indicator="temperature",
                     days=7, today=TODAY)

    assert _gap_keys(result) == [(_day(2).isoformat(), "RU-MOW", 18)]
    assert _counts(result) == (7, 21, 146, 1)


@requires_postgres
def test_a_full_day_is_not(scratch_db):
    """Otherwise the report is satisfied by one that flags everything."""
    engine, url = scratch_db
    _grid(engine, _window_days(7), overrides={
        (_day(2), "RU-MOW"): [h * 60 for h in range(23)],
    })

    result = _report(url, source="openmeteo", indicator="temperature",
                     days=7, today=TODAY)

    assert _gap_keys(result) == []
    assert _counts(result) == (7, 21, 147, 0)


@requires_postgres
def test_full_grid_has_zero_gaps(scratch_db):
    engine, url = scratch_db
    _grid(engine, _window_days(7))

    result = _report(url, source="openmeteo", indicator="temperature",
                     days=7, today=TODAY)

    assert _counts(result) == (7, 21, 147, 0)


@requires_postgres
def test_an_empty_database_is_every_point_day_a_gap(scratch_db):
    """No rows at all: every expected point-day is a gap, not zero gaps.

    The earlier behaviour answered zero here, because no group existed to be
    short. The denominator now says what was expected and what was missing.
    """
    _, url = scratch_db

    result = _report(url, source="openmeteo", indicator="temperature",
                     days=7, today=TODAY)

    assert _counts(result) == (7, 21, 0, 147)
    assert len(result.gaps) == 147
    assert all(g.observations == 0 for g in result.gaps)


# ---- a point with no rows: the case the grid exists for -------------------

@requires_postgres
def test_a_point_day_with_no_rows_is_counted_as_a_gap(scratch_db):
    """RU-MOW supplies all of 2026-10-18 and nothing on 2026-10-19.

    The day with no rows forms no GROUP BY group; the grid must still expect it.
    """
    engine, url = scratch_db
    _grid(engine, _window_days(3), overrides={(_day(1), "RU-MOW"): None})

    result = _report(url, source="openmeteo", indicator="temperature",
                     days=3, today=TODAY)

    assert ("2026-10-19", "RU-MOW", 0) in _gap_keys(result)
    assert _counts(result) == (3, 21, 62, 1)


@requires_postgres
def test_a_point_with_no_rows_for_the_whole_window_is_a_gap_each_day(scratch_db):
    engine, url = scratch_db
    _grid(engine, _window_days(7), overrides={
        (day, "RU-MOW"): None for day in _window_days(7)
    })

    result = _report(url, source="openmeteo", indicator="temperature",
                     days=7, today=TODAY)

    assert _gap_keys(result) == [
        (day.isoformat(), "RU-MOW", 0) for day in reversed(_window_days(7))
    ]
    assert _counts(result) == (7, 21, 140, 7)


@requires_postgres
def test_a_full_day_outage_of_all_points_is_one_gap_per_point(scratch_db):
    engine, url = scratch_db
    outage = _day(3)
    _grid(engine, _window_days(7), overrides={
        (outage, code): None for code in DECLARED
    })

    result = _report(url, source="openmeteo", indicator="temperature",
                     days=7, today=TODAY)

    assert result.gap_count == 21
    assert {g.day for g in result.gaps} == {outage.isoformat()}
    assert {g.region_id for g in result.gaps} == set(DECLARED)
    assert all(g.observations == 0 for g in result.gaps)
    assert _counts(result) == (7, 21, 126, 21)


# ---- repeated rows and hours ----------------------------------------------

@requires_postgres
def test_a_day_thin_in_hours_is_reported_though_it_has_enough_rows(scratch_db):
    """Twenty rows in eighteen distinct hours: the rows clear 19, the hours do not.

    Counting rows would pass the day; counting hours reports it short at 18.
    """
    engine, url = scratch_db
    minutes = [h * 60 for h in range(18)] + [15, 75]
    assert len(minutes) == 20 and len({m // 60 for m in minutes}) == 18
    _grid(engine, _window_days(7), overrides={(_day(2), "RU-MOW"): minutes})

    result = _report(url, source="openmeteo", indicator="temperature",
                     days=7, today=TODAY)

    assert _gap_keys(result) == [(_day(2).isoformat(), "RU-MOW", 18)]


@requires_postgres
def test_repeated_samples_do_not_manufacture_a_complete_day(scratch_db):
    """Nineteen rows inside four hours: by rows a complete day, by hours a sixth."""
    engine, url = scratch_db
    minutes = [h * 60 + m for h in range(4) for m in (0, 12, 24, 36, 48)][:19]
    assert len(minutes) == 19 and len({m // 60 for m in minutes}) == 4
    _grid(engine, _window_days(7), overrides={(_day(3), "RU-SPE"): minutes})

    result = _report(url, source="openmeteo", indicator="temperature",
                     days=7, today=TODAY)

    assert _gap_keys(result) == [(_day(3).isoformat(), "RU-SPE", 4)]
    assert result.complete_days == 146


# ---- rows that are not expected ------------------------------------------

@requires_postgres
def test_unknown_region_rows_are_ignored(scratch_db):
    """Rows for a region the registry does not declare add no grid rows."""
    engine, url = scratch_db
    _grid(engine, _window_days(7))
    strays = []
    for day in _window_days(7):
        strays += _rows(day, "XX-NOPE", HOURLY, "stray")
        strays += _rows(day, "ZZZ", HOURLY, "stray")
    _insert(engine, strays)

    result = _report(url, source="openmeteo", indicator="temperature",
                     days=7, today=TODAY)

    assert _counts(result) == (7, 21, 147, 0)
    assert all(g.region_id in DECLARED for g in result.gaps)


@requires_postgres
def test_today_is_excluded(scratch_db):
    """Incomplete by construction: no day for it appears, complete or short."""
    engine, url = scratch_db
    _grid(engine, _window_days(7))
    _insert(engine, _rows(TODAY, "RU-MOW", [0, 60, 120], "today"))

    result = _report(url, source="openmeteo", indicator="temperature",
                     days=7, today=TODAY)

    assert result.days_examined == 7
    assert result.gap_count == 0
    assert all(g.day != TODAY.isoformat() for g in result.gaps)


@requires_postgres
def test_the_window_is_honoured(scratch_db):
    engine, url = scratch_db
    _grid(engine, _window_days(7), overrides={
        (_day(2), "RU-MOW"): [h * 60 for h in range(18)],
    })
    _insert(engine, _rows(_day(40), "RU-SPE", [h * 60 for h in range(18)], "old"))

    result = _report(url, source="openmeteo", indicator="temperature",
                     days=7, today=TODAY)

    assert {g.region_id for g in result.gaps} == {"RU-MOW"}
    assert _counts(result) == (7, 21, 146, 1)


# ---- the declared start of a point ----------------------------------------

@requires_postgres
def test_a_point_is_expected_only_from_its_since(scratch_db, monkeypatch):
    """RU-SPE is declared from TODAY-3. Silence before that day is not a gap;
    silence on it is the first gap, and it is the only one."""
    import dataclasses

    from app.services.environmental import regions

    engine, url = scratch_db
    key = ("openmeteo", "temperature")
    since = _day(3)
    declared = tuple(
        dataclasses.replace(p, since=since) if p.code == "RU-SPE" else p
        for p in regions.COVERAGE_REGISTRY[key]
    )
    monkeypatch.setitem(regions.COVERAGE_REGISTRY, key, declared)

    _grid(engine, _window_days(7), overrides={
        (day, "RU-SPE"): None for day in _window_days(7) if day <= since
    })

    result = _report(url, source="openmeteo", indicator="temperature",
                     days=7, today=TODAY)

    assert _gap_keys(result) == [(since.isoformat(), "RU-SPE", 0)]
    assert _counts(result) == (7, 21, 142, 1)


# ---- the session's TimeZone ------------------------------------------------
#
# Every calendar fact above is a UTC fact. `event_time` is timestamptz, so a
# session in another zone would read `event_time::date` and `date_trunc` in
# that zone, and one complete UTC day would split into two short local ones.

@requires_postgres
@pytest.mark.parametrize("session_timezone", ZONES)
def test_one_complete_utc_day_is_one_complete_day_in_every_session(
        scratch_db, session_timezone):
    engine, url = scratch_db
    _grid(engine, _window_days(3), minutes=TWICE)

    result = _report(url, session_timezone, source="openmeteo",
                     indicator="temperature", days=3, today=TODAY)

    assert _counts(result) == (3, 21, 63, 0), (
        f"with the session in {session_timezone}: {_counts(result)}")


@requires_postgres
@pytest.mark.parametrize("session_timezone", ZONES)
def test_a_short_utc_day_is_one_gap_on_its_utc_date_in_every_session(
        scratch_db, session_timezone):
    engine, url = scratch_db
    _grid(engine, _window_days(3), minutes=TWICE, overrides={
        (_day(2), "RU-MOW"): [h * 60 + m for h in range(18) for m in (10, 40)],
    })

    result = _report(url, session_timezone, source="openmeteo",
                     indicator="temperature", days=3, today=TODAY)

    assert _gap_keys(result) == [(_day(2).isoformat(), "RU-MOW", 18)], (
        f"with the session in {session_timezone}: {_gap_keys(result)}")


@requires_postgres
@pytest.mark.parametrize("session_timezone", ZONES)
def test_the_window_edges_are_utc_midnights_in_every_session(
        scratch_db, session_timezone):
    """Rows on the day before the window and on today are outside it, in every zone."""
    engine, url = scratch_db
    _grid(engine, _window_days(3), minutes=TWICE)
    _insert(engine, _rows(_day(4), "RU-MOW", [23 * 60 + 50], "before"))
    _insert(engine, _rows(TODAY, "RU-MOW", [10], "today"))

    result = _report(url, session_timezone, source="openmeteo",
                     indicator="temperature", days=3, today=TODAY)

    assert _counts(result) == (3, 21, 63, 0), (
        f"with the session in {session_timezone}: {_counts(result)}")


# ---- the M3 window: clock start, rolling bound, today ----------------------

@requires_postgres
def test_the_day_before_the_clock_is_excluded_when_bounded(scratch_db):
    """2026-10-10: four hours on the restored deployment's first day."""
    from app.services.observation_coverage import M3_CLOCK_START

    engine, url = scratch_db
    clock_days = [date(2026, 10, 11) + timedelta(days=k) for k in range(9)]
    _grid(engine, clock_days)
    _insert(engine, _rows(date(2026, 10, 10), "RU-MOW", [h * 60 for h in range(4)], "pre"))

    bounded = _report(url, source="openmeteo", indicator="temperature",
                      days=30, not_before=M3_CLOCK_START, today=TODAY)

    assert _counts(bounded) == (9, 21, 189, 0), (
        f"2026-10-10 is before the clock, but the bounded report was {_counts(bounded)}")


@requires_postgres
def test_the_day_before_the_clock_is_reported_without_the_bound(scratch_db):
    """The same data, unbounded: the day is examined, and its gap appears."""
    engine, url = scratch_db
    _insert(engine, _rows(date(2026, 10, 10), "RU-MOW", [h * 60 for h in range(4)], "pre"))

    plain = _report(url, source="openmeteo", indicator="temperature",
                    days=30, today=TODAY)

    assert plain.days_examined == 30
    assert ("2026-10-10", "RU-MOW", 4) in _gap_keys(plain)


@requires_postgres
def test_a_short_day_on_the_clock_start_is_counted(scratch_db):
    from app.services.observation_coverage import M3_CLOCK_START

    engine, url = scratch_db
    clock_days = [date(2026, 10, 11) + timedelta(days=k) for k in range(9)]
    _grid(engine, clock_days, overrides={
        (date(2026, 10, 11), "RU-MOW"): [h * 60 for h in range(18)],
    })

    result = _report(url, source="openmeteo", indicator="temperature",
                     days=30, not_before=M3_CLOCK_START, today=TODAY)

    assert _gap_keys(result) == [("2026-10-11", "RU-MOW", 18)]


@requires_postgres
def test_after_thirty_days_the_rolling_bound_wins_over_the_clock(scratch_db):
    """From 2026-11-20 the window is 2026-10-21 onward; the clock is earlier."""
    from app.services.observation_coverage import M3_CLOCK_START

    engine, url = scratch_db
    later = date(2026, 11, 20)
    window = [later - timedelta(days=k) for k in range(30, 0, -1)]
    _grid(engine, window, overrides={
        (date(2026, 10, 25), "RU-MOW"): [h * 60 for h in range(18)],
    })
    _insert(engine, _rows(date(2026, 10, 15), "RU-MOW", [h * 60 for h in range(18)], "old"))

    result = _report(url, source="openmeteo", indicator="temperature",
                     days=30, not_before=M3_CLOCK_START, today=later)

    assert _gap_keys(result) == [("2026-10-25", "RU-MOW", 18)], (
        "the 2026-10-15 day lies inside the clock but outside the last 30 days, "
        "so it must not be examined"
    )
    assert result.days_examined == 30


@requires_postgres
def test_today_is_excluded_even_with_rows_on_it(scratch_db):
    from app.services.observation_coverage import M3_CLOCK_START

    engine, url = scratch_db
    clock_days = [date(2026, 10, 11) + timedelta(days=k) for k in range(9)]
    _grid(engine, clock_days)
    _insert(engine, _rows(TODAY, "RU-MOW", [0, 60, 120], "today"))
    # 23:50 UTC is still today in UTC: the upper bound is UTC midnight.
    _insert(engine, _rows(TODAY, "RU-SPE", [23 * 60 + 50], "late"))

    result = _report(url, source="openmeteo", indicator="temperature",
                     days=30, not_before=M3_CLOCK_START, today=TODAY)

    assert result.days_examined == 9
    assert result.gap_count == 0
    assert all(g.day != TODAY.isoformat() for g in result.gaps)


@requires_postgres
@pytest.mark.parametrize("session_timezone", ZONES)
def test_the_bounded_window_is_utc_in_every_session(scratch_db, session_timezone):
    """Complete day, short day, and today, under each session zone.

    The clock bound is applied in UTC too: 2026-10-11 00:10 UTC is in the window
    and stays in it in Kolkata, where it is 05:40 local.
    """
    from app.services.observation_coverage import M3_CLOCK_START

    engine, url = scratch_db
    clock_days = [date(2026, 10, 11) + timedelta(days=k) for k in range(9)]
    _grid(engine, clock_days, minutes=TWICE, overrides={
        (date(2026, 10, 13), "RU-MOW"): [h * 60 + m for h in range(18) for m in (10, 40)],
    })
    _insert(engine, _rows(TODAY, "RU-MOW", [23 * 60 + 50], "today"))

    result = _report(url, session_timezone, source="openmeteo",
                     indicator="temperature", days=30,
                     not_before=M3_CLOCK_START, today=TODAY)

    assert _counts(result) == (9, 21, 188, 1), (
        f"with the session in {session_timezone}: {_counts(result)}, "
        f"gaps {_gap_keys(result)}")
    assert _gap_keys(result) == [("2026-10-13", "RU-MOW", 18)], (
        f"with the session in {session_timezone}: gaps {_gap_keys(result)}")


# ---- the API and the scheduler report the same window ----------------------

@requires_postgres
def test_the_api_and_the_scheduler_report_the_same_window(scratch_db, monkeypatch):
    """Both read coverage_report; the window is the same, so the answer must be.

    The route has a rolling `days` window and no clock bound; the scheduler is
    bound to the clock. Asking the route for the clock's own length makes the two
    windows identical, and then the gauges and the route must agree on every
    number, including the gaps themselves.
    """
    import app.api.infra as infra
    import app.database as database
    import app.scheduler as scheduler_module
    import app.services.observation_coverage as coverage
    from prometheus_client import REGISTRY
    from sqlalchemy.orm import sessionmaker

    from app.services.observation_coverage import M3_CLOCK_START

    engine, _url = scratch_db
    clock_length = (TODAY - M3_CLOCK_START).days
    assert clock_length == 9
    clock_days = _window_days(clock_length)
    _grid(engine, clock_days, overrides={
        (date(2026, 10, 13), "RU-MOW"): [h * 60 for h in range(18)],
        (date(2026, 10, 15), "RU-SPE"): None,
    })

    monkeypatch.setattr(coverage, "utc_today", lambda: TODAY)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))

    api = infra.observation_coverage(
        _admin=None, source="openmeteo", indicator="temperature", days=clock_length)
    job = scheduler_module.scheduled_observation_coverage()

    assert job["status"] == "ok"
    assert (job["gap_count"], job["days_examined"], job["points_examined"]) == (
        api.gap_count, api.days_examined, api.points_examined)
    assert _counts(api) == (9, 21, 189 - 2, 2)

    labels = {"source": "openmeteo", "indicator": "temperature"}
    assert REGISTRY.get_sample_value(
        "sora_observation_coverage_gap_days", labels) == api.gap_count
    assert REGISTRY.get_sample_value(
        "sora_observation_coverage_days_examined", labels) == api.days_examined
    assert REGISTRY.get_sample_value(
        "sora_observation_coverage_points_examined", labels) == api.points_examined
    assert REGISTRY.get_sample_value(
        "sora_observation_coverage_last_success_timestamp_seconds", labels) > 0

    scheduler_report = coverage.coverage_report(
        source="openmeteo", indicator="temperature", days=30,
        not_before=M3_CLOCK_START)
    assert _gap_keys(scheduler_report) == _gap_keys(api)


@requires_postgres
def test_exactly_nineteen_hours_is_complete_and_eighteen_is_a_gap(scratch_db):
    """The threshold, at its own boundary: 19 of 24 hours passes, 18 does not.

    Two points on two different days of a full 7-day grid. RU-MOW has exactly 19
    distinct UTC hours and must not be a gap; RU-SPE has exactly 18 and must be
    the one and only gap. A comparison written `<=` instead of `<` fails here.
    """
    engine, url = scratch_db
    nineteen_day, eighteen_day = _day(2), _day(4)
    _grid(engine, _window_days(7), overrides={
        (nineteen_day, "RU-MOW"): [h * 60 for h in range(19)],
        (eighteen_day, "RU-SPE"): [h * 60 for h in range(18)],
    })

    result = _report(url, source="openmeteo", indicator="temperature",
                     days=7, today=TODAY)

    assert _gap_keys(result) == [(eighteen_day.isoformat(), "RU-SPE", 18)]
    assert _counts(result) == (7, 21, 146, 1)
