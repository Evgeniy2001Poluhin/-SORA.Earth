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
from datetime import date, datetime, timedelta, timezone

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


def _fill_at(engine, day_offset, region, minutes_past_midnight):
    """Observations at arbitrary offsets within one past UTC day.

    `_fill` above puts exactly one row in each hour, so it cannot tell a day
    covered by 20 hours from a day covered by 20 rows crammed into 18 -- and
    those are the two states this endpoint exists to distinguish. The ingester
    produces the second: it reads Open-Meteo's `current` block, whose `time`
    moves at 15-minute resolution, so two runs more than 15 minutes apart in one
    hour write two rows. Every deployment causes such a run, because
    auto_openmeteo_ingestion is in RUN_IMMEDIATELY_ON_STARTUP.
    """
    base = (datetime.now(timezone.utc) - timedelta(days=day_offset)
            ).replace(hour=0, minute=0, second=0, microsecond=0)
    with engine.begin() as conn:
        for index, minutes in enumerate(minutes_past_midnight):
            conn.execute(INSERT, {
                "region": region,
                "rid": f"{region}-{day_offset}-at-{index}",
                "event_time": base + timedelta(minutes=minutes),
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


@requires_postgres
def test_a_day_thin_in_hours_is_reported_though_it_has_enough_rows(scratch_db):
    """The defect this endpoint was reported to have, measured on production.

    Twenty rows, and every one of them real -- but they fall in eighteen
    distinct hours, because two hours were sampled twice. Counting rows the day
    passes at 20 >= 19; counting hours it is short at 18, six hours of the day
    are missing, and §3 of the M3 declaration says the day is absent.

    On production 2026-09-19 this was not hypothetical: over the first sixteen
    days of the restarted clock, 2026-09-09, 2026-09-10 and 2026-09-14 each had
    a point below the rule in hours and at or above it in rows, so the endpoint
    reported nothing. 3620 point-hours held more than one row.
    """
    engine, _ = scratch_db
    # 18 distinct hours; hours 0 and 1 sampled twice, 15 minutes apart.
    minutes = [h * 60 for h in range(18)] + [15, 75]
    assert len(minutes) == 20
    assert len({m // 60 for m in minutes}) == 18
    _fill_at(engine, 2, "RU-MOW", minutes)

    result = _coverage(engine, source="openmeteo", indicator="temperature", days=30)

    assert result.gap_count == 1, (
        "a day covering 18 of 24 hours was not reported, because its 20 rows "
        "cleared a threshold meant for hours. This is the failure mode that "
        "makes a quiet dip in coverage invisible while it moves the M3 date."
    )
    assert result.gaps[0].observations == 18, (
        f"the report says {result.gaps[0].observations}, which is the row count; "
        f"the number that decides whether the day is usable is the hour count"
    )


@requires_postgres
def test_repeated_samples_do_not_manufacture_a_complete_day(scratch_db):
    """The same defect at its worst: enough rows, almost none of the day.

    Nineteen rows inside four hours. By rows it is a complete day; by hours it
    covers a sixth of one. Without this, a test suite could pass on an endpoint
    that counts anything at all, as long as it counted enough of it.
    """
    engine, _ = scratch_db
    minutes = [h * 60 + m for h in range(4) for m in (0, 12, 24, 36, 48)][:19]
    assert len(minutes) == 19
    assert len({m // 60 for m in minutes}) == 4
    _fill_at(engine, 3, "RU-SPE", minutes)

    result = _coverage(engine, source="openmeteo", indicator="temperature", days=30)

    assert result.gap_count == 1
    assert result.gaps[0].observations == 4
    assert result.complete_days == 0


# ---- the session's TimeZone ------------------------------------------------
#
# Every test above runs in the server's default zone, which is UTC on the CI
# service and in the postgres:16-alpine image production runs. So each of them
# would pass under a query that counted the *session's* calendar day instead of
# the UTC day the declaration defines -- and the query did exactly that.
# `event_time` is timestamptz, and `event_time::date`,
# `date_trunc('hour', event_time)` and the zone-less `(now() at time zone
# 'utc')::date` edges are all read in the session's zone. Measured against
# PostgreSQL 16 before the fix, one complete UTC day, two rows an hour:
#
#   Asia/Tokyo           2 days, 0 complete, gaps (9 h, 15 h)
#   America/Los_Angeles  2 days, 0 complete, gaps (17 h, 7 h)
#   Asia/Kolkata         2 days, 1 complete, 1 gap (6 h)
#
# and an 18-hour UTC day -- a real gap -- reported with *no* gap under
# Asia/Kolkata: at +05:30 each UTC hour's two rows fall in two local hours, so
# the hours count doubled. The failure this endpoint exists to surface, hidden.
# Latent where sessions are UTC; nothing in the application sets one.

ZONES = ["UTC", "Asia/Tokyo", "America/Los_Angeles", "Asia/Kolkata"]

#: Two rows in every hour of a UTC day, at :10 and :40 -- 48 rows, 24 hours.
TWICE_AN_HOUR = [h * 60 + m for h in range(24) for m in (10, 40)]


def _coverage_in(url, session_timezone, **params):
    import app.api.infra as infra
    import app.database as database
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        url, connect_args={"options": "-c TimeZone=%s" % session_timezone})
    original = database.SessionLocal
    database.SessionLocal = sessionmaker(bind=engine)
    try:
        return infra.observation_coverage(_admin=None, **params)
    finally:
        database.SessionLocal = original
        engine.dispose()


def _gaps(result):
    return [(g.day, g.observations) for g in result.gaps]


@requires_postgres
@pytest.mark.parametrize("session_timezone", ZONES)
def test_one_complete_utc_day_is_one_complete_day_in_every_session(
        scratch_db, session_timezone):
    engine, url = scratch_db
    _fill_at(engine, 2, "RU-MOW", TWICE_AN_HOUR)

    result = _coverage_in(url, session_timezone, source="openmeteo",
                          indicator="temperature", days=30)

    assert (result.days_examined, result.complete_days, result.gap_count) == (1, 1, 0), (
        f"with the session in {session_timezone}, one complete UTC day was "
        f"reported as days_examined={result.days_examined}, complete_days="
        f"{result.complete_days}, gaps={_gaps(result)}"
    )


@requires_postgres
@pytest.mark.parametrize("session_timezone", ZONES)
def test_a_short_utc_day_is_one_gap_on_its_utc_date_in_every_session(
        scratch_db, session_timezone):
    engine, url = scratch_db
    _fill_at(engine, 2, "RU-MOW", [h * 60 + m for h in range(18) for m in (10, 40)])
    day = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()

    result = _coverage_in(url, session_timezone, source="openmeteo",
                          indicator="temperature", days=30)

    assert _gaps(result) == [(day, 18)], (
        f"with the session in {session_timezone}, a UTC day with 18 of 24 "
        f"hours was reported as gaps {_gaps(result)}"
    )


@requires_postgres
@pytest.mark.parametrize("session_timezone", ZONES)
def test_the_window_edges_are_utc_midnights_in_every_session(
        scratch_db, session_timezone):
    engine, url = scratch_db
    _fill_at(engine, 0, "RU-MOW", TWICE_AN_HOUR)   # today: excluded by construction
    _fill_at(engine, 5, "RU-MOW", TWICE_AN_HOUR)   # outside a three-day window
    _fill_at(engine, 1, "RU-MOW", TWICE_AN_HOUR)   # inside

    result = _coverage_in(url, session_timezone, source="openmeteo",
                          indicator="temperature", days=3)

    assert (result.days_examined, result.complete_days) == (1, 1), (
        f"with the session in {session_timezone}: days_examined="
        f"{result.days_examined}, complete_days={result.complete_days}, "
        f"gaps={_gaps(result)}"
    )


# ---- the M3 window: clock start, rolling bound, today --------------------
#
# These call coverage_report directly with fixed dates, so each case states its
# own calendar instead of depending on the day the suite happens to run.

TODAY = date(2026, 10, 20)


def _fill_on(engine, day, region, minutes):
    """Observations at the given minute offsets within one UTC calendar day."""
    base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    with engine.begin() as conn:
        for index, minute in enumerate(minutes):
            conn.execute(INSERT, {
                "region": region,
                "rid": f"{region}-{day.isoformat()}-{index}",
                "event_time": base + timedelta(minutes=minute),
            })


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


@requires_postgres
def test_the_day_before_the_clock_is_excluded_when_bounded(scratch_db):
    """2026-10-10: four hours on the restored deployment's first day."""
    from app.services.observation_coverage import M3_CLOCK_START

    engine, url = scratch_db
    _fill_on(engine, date(2026, 10, 10), "RU-MOW", [h * 60 for h in range(4)])

    bounded = _report(url, source="openmeteo", indicator="temperature",
                      days=30, not_before=M3_CLOCK_START, today=TODAY)
    assert (bounded.days_examined, bounded.gap_count) == (0, 0), (
        f"2026-10-10 is before the clock, but the bounded report examined "
        f"{bounded.days_examined} day(s) with gaps {_gap_keys(bounded)}"
    )


@requires_postgres
def test_the_day_before_the_clock_is_reported_without_the_bound(scratch_db):
    """The same data, unbounded: the API's behaviour is unchanged.

    Without this, the exclusion above could be a window that drops everything.
    """
    engine, url = scratch_db
    _fill_on(engine, date(2026, 10, 10), "RU-MOW", [h * 60 for h in range(4)])

    plain = _report(url, source="openmeteo", indicator="temperature",
                    days=30, today=TODAY)
    assert _gap_keys(plain) == [("2026-10-10", "RU-MOW", 4)]


@requires_postgres
def test_a_short_day_on_the_clock_start_is_counted(scratch_db):
    from app.services.observation_coverage import M3_CLOCK_START

    engine, url = scratch_db
    _fill_on(engine, date(2026, 10, 11), "RU-MOW", [h * 60 for h in range(18)])

    result = _report(url, source="openmeteo", indicator="temperature",
                     days=30, not_before=M3_CLOCK_START, today=TODAY)

    assert _gap_keys(result) == [("2026-10-11", "RU-MOW", 18)]


@requires_postgres
def test_after_thirty_days_the_rolling_bound_wins_over_the_clock(scratch_db):
    """From 2026-11-20 the window is 2026-10-21 onward; the clock is earlier."""
    from app.services.observation_coverage import M3_CLOCK_START

    engine, url = scratch_db
    later = date(2026, 11, 20)
    _fill_on(engine, date(2026, 10, 15), "RU-MOW", [h * 60 for h in range(18)])
    _fill_on(engine, date(2026, 10, 25), "RU-MOW", [h * 60 for h in range(18)])

    result = _report(url, source="openmeteo", indicator="temperature",
                     days=30, not_before=M3_CLOCK_START, today=later)

    assert _gap_keys(result) == [("2026-10-25", "RU-MOW", 18)], (
        "the 2026-10-15 day lies inside the clock but outside the last 30 days, "
        "so it must not be examined"
    )


@requires_postgres
def test_today_is_excluded_even_with_rows_on_it(scratch_db):
    from app.services.observation_coverage import M3_CLOCK_START

    engine, url = scratch_db
    _fill_on(engine, TODAY, "RU-MOW", [0, 60, 120])
    # 23:50 UTC is still today in UTC: the upper bound is UTC midnight.
    _fill_on(engine, TODAY, "RU-SPE", [23 * 60 + 50])

    result = _report(url, source="openmeteo", indicator="temperature",
                     days=30, not_before=M3_CLOCK_START, today=TODAY)

    assert (result.days_examined, result.gap_count) == (0, 0)


@requires_postgres
@pytest.mark.parametrize("session_timezone", ZONES)
def test_the_bounded_window_is_utc_in_every_session(scratch_db, session_timezone):
    """Complete day, short day, and today, under each session zone.

    The clock bound is applied in UTC too: 2026-10-11 00:10 UTC is in the window,
    and it stays in the window in Kolkata, where it is 05:40 local.
    """
    from app.services.observation_coverage import M3_CLOCK_START

    engine, url = scratch_db
    _fill_on(engine, date(2026, 10, 11), "RU-MOW", TWICE_AN_HOUR)              # complete
    _fill_on(engine, date(2026, 10, 13), "RU-MOW",
             [h * 60 + m for h in range(18) for m in (10, 40)])                # short
    _fill_on(engine, TODAY, "RU-MOW", [23 * 60 + 50])                           # excluded

    result = _report(url, session_timezone, source="openmeteo",
                     indicator="temperature", days=30,
                     not_before=M3_CLOCK_START, today=TODAY)

    assert (result.days_examined, result.complete_days) == (2, 1), (
        f"with the session in {session_timezone}: days_examined="
        f"{result.days_examined}, complete_days={result.complete_days}, "
        f"gaps={_gap_keys(result)}"
    )
    assert _gap_keys(result) == [("2026-10-13", "RU-MOW", 18)], (
        f"with the session in {session_timezone}: gaps {_gap_keys(result)}"
    )


# ---- the missing-day probe: deliberately not fixed ---------------------------
#
# GROUP BY sees only the (day, point) pairs that have at least one row. A point
# that supplied nothing on a day produces no group, so no gap is reported for
# it, however long it was silent. Zero gaps therefore means no *incomplete*
# group existed, not that every point was present every day. Whether and how to
# count a wholly absent point-day is pending a decision; this test records the
# gap so it cannot be forgotten, and is strict so that a fix must remove the
# marker.

@requires_postgres
@pytest.mark.xfail(
    strict=True,
    reason="GROUP BY cannot see a point-day with no rows; decision pending",
)
def test_a_point_day_with_no_rows_is_counted_as_a_gap(scratch_db):
    """RU-MOW supplies all of 2026-10-18 and nothing on 2026-10-19.

    Gap detection should count 2026-10-19 for RU-MOW with zero observations.
    """
    from app.services.observation_coverage import M3_CLOCK_START

    engine, url = scratch_db
    _fill_on(engine, date(2026, 10, 18), "RU-MOW", list(range(0, 24 * 60, 60)))

    result = _report(url, source="openmeteo", indicator="temperature",
                     days=30, not_before=M3_CLOCK_START, today=TODAY)

    assert ("2026-10-19", "RU-MOW", 0) in _gap_keys(result)
