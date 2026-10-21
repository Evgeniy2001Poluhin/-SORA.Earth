"""Observation coverage: how many distinct UTC hours each declared point supplied per day.

Lives in app/services, not app/api, because the scheduler publishes these numbers
as Prometheus gauges. The scheduler runs without the API's auth configuration
(SORA_JWT_SECRET), and importing app.api.infra pulled in app.auth, which refuses
to import in production without that secret. This module must not import
app.api or app.auth. The route in app/api/infra.py calls coverage_report from
here, so the SQL that decides whether a day counts exists exactly once.
"""
from datetime import date, datetime, timedelta, timezone

from app.schemas import CoverageGap, ObservationCoverage
from app.services.environmental.regions import COVERAGE_REGISTRY, DeclaredPoint




#: Hourly sources are expected to supply 24 observations per point per day.
#: The M3 declaration binds usability to 80% of that, reusing MIN_COVERAGE from
#: the entry gate rather than introducing a second number that could drift.
EXPECTED_PER_DAY = 24

#: The first UTC day the M3 section 7 clock counts. Declared in
#: docs/M3_FORECAST_DECLARATION.md, "Clock start: 2026-10-11, measured" (Amendment
#: 1.4); tests/test_development_roadmap_dates.py carries the same value as
#: CLOCK_START, and a test holds the two equal. Change it only with a documented
#: restart of the clock. A window that starts later hides a short day from the
#: gauges; one that starts earlier reports days no gate will use.
M3_CLOCK_START = date(2026, 10, 11)


class CoverageNotSupported(Exception):
    """No point set is declared for this source and indicator.

    Raised instead of answering. A count for a source with no declared grid
    would be a zero that means nothing, so the route answers 422 and the
    scheduler logs a warning and leaves its gauges unset.
    """


def declared_points(source: str, indicator: str) -> tuple[DeclaredPoint, ...]:
    """The points a complete day requires for this source and indicator."""
    points = COVERAGE_REGISTRY.get((source, indicator))
    if points is None:
        raise CoverageNotSupported(
            f"coverage is not supported for source={source!r} "
            f"indicator={indicator!r}: no point set is declared for it")
    return points


def coverage_window(days: int, not_before: date | None, today: date) -> tuple[date, date]:
    """The UTC days [lower, upper) that a coverage report examines.

    `upper` is today, excluded: today is incomplete by construction. `lower` is
    `days` back from today, or `not_before` if that is later. The rolling bound
    and the clock bound both apply, and whichever is later wins.
    """
    if not_before is not None and not_before > today:
        raise ValueError("not_before is after today")
    lower = today - timedelta(days=days)
    if not_before is not None and not_before > lower:
        lower = not_before
    return lower, today


def utc_today() -> date:
    """Today's UTC date. A function so a test can state the calendar."""
    return datetime.now(timezone.utc).date()


def coverage_report(source: str = "openmeteo",
                    indicator: str = "temperature",
                    days: int = 30,
                    not_before: date | None = None,
                    today: date | None = None) -> ObservationCoverage:
    """The query behind the route, callable without a request.

    The expected grid is every UTC day in the window crossed with every point the
    registry declares for (source, indicator) that is due on that day. Each
    expected point-day is then matched against the distinct UTC hours observed
    for it, and a point-day with no rows at all is an expected pair with zero
    hours, so it is a gap. Counting only the groups that existed would make a
    point that went silent for a whole day invisible -- which is the defect this
    replaced.

    Units, all stated so a reader can tell a zero from an empty check:
      days_examined    calendar UTC days in the window
      points_examined  declared points due on at least one day of the window
      complete_days    expected point-days with at least 19 distinct hours
      gap_count        expected point-days below that, including those with none
    Rows for a region the registry does not declare are ignored, and never add
    to the grid.

    `not_before` is an optional lower bound (the scheduler passes M3_CLOCK_START);
    left None, the window is the plain `days` back. `today` exists so a test can
    state the calendar; production leaves it None and gets the UTC date now.
    Raises CoverageNotSupported for a source or indicator with no declared points.
    """
    from sqlalchemy import text

    from app.database import SessionLocal
    from app.services.forecasting.entry_conditions import MIN_COVERAGE

    points = declared_points(source, indicator)
    required = int(EXPECTED_PER_DAY * MIN_COVERAGE)
    if today is None:
        today = utc_today()
    lower, upper = coverage_window(days, not_before, today)
    codes = [p.code for p in points]
    since = [p.since for p in points]

    db = SessionLocal()
    try:
        rows = db.execute(text(
            """
            -- The expected grid. One row per (UTC day, declared point) with the
            -- point due that day: generate_series over the window, crossed with
            -- the registry, which arrives as two parallel arrays.
            WITH days AS (
                SELECT d::date AS day
                  FROM generate_series(CAST(:lower AS date),
                                       CAST(:upper AS date) - 1,
                                       interval '1 day') AS d
            ),
            declared AS (
                SELECT code, since
                  FROM unnest(CAST(:codes AS text[]), CAST(:since AS date[]))
                       AS p(code, since)
            ),
            expected AS (
                SELECT days.day, declared.code
                  FROM days
                  CROSS JOIN declared
                 WHERE days.day >= declared.since
            ),
            -- DISTINCT hours, not rows (#179, amendment 1.3). The source is
            -- polled roughly hourly but stamped from Open-Meteo's `current`
            -- block, whose time moves at 15-minute resolution, so one hour can
            -- hold several rows -- every deployment adds one, since
            -- auto_openmeteo_ingestion runs at startup. Counting rows let a day
            -- covering 18 hours clear a threshold meant for 19 of 24, which is
            -- exactly the quiet dip this endpoint exists to surface.
            --
            -- UTC days and UTC hours, whatever the session's TimeZone. Both
            -- `event_time::date` and `date_trunc` read in the session's zone, so
            -- `event_time at time zone 'utc'` is the UTC wall clock used here,
            -- and the window edges are UTC midnights bound as dates.
            observed AS (
                SELECT (event_time at time zone 'utc')::date AS day,
                       region_id AS code,
                       count(DISTINCT date_trunc('hour', event_time at time zone 'utc'))
                           AS hours
                  FROM environmental_observations
                 WHERE source = :source
                   AND indicator = :indicator
                   AND temporal_kind = 'observed'
                   AND event_time IS NOT NULL
                   AND event_time >= CAST(:lower AS date)::timestamp at time zone 'utc'
                   AND event_time <  CAST(:upper AS date)::timestamp at time zone 'utc'
                   AND region_id = ANY(CAST(:codes AS text[]))
                 GROUP BY 1, 2
            )
            SELECT to_char(expected.day, 'YYYY-MM-DD') AS day,
                   expected.code AS region_id,
                   COALESCE(observed.hours, 0) AS observations
              FROM expected
              LEFT JOIN observed
                     ON observed.day = expected.day
                    AND observed.code = expected.code
             ORDER BY expected.day DESC, expected.code
            """
        ), {"source": source, "indicator": indicator,
            "lower": lower, "upper": upper,
            "codes": codes, "since": since}).all()

        gaps = [
            CoverageGap(day=day, region_id=region, indicator=indicator,
                        observations=int(count), required=required)
            for day, region, count in rows if int(count) < required
        ]
        return ObservationCoverage(
            source=source,
            indicator=indicator,
            required_per_day=required,
            days_examined=max(0, (upper - lower).days),
            points_examined=(sum(1 for p in points if p.since < upper)
                             if lower < upper else 0),
            complete_days=len(rows) - len(gaps),
            gap_count=len(gaps),
            gaps=gaps,
        )
    finally:
        db.close()
