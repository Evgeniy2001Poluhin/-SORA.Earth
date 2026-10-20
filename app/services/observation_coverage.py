"""Observation coverage: how many hourly observations each point supplied per day.

Lives in app/services, not app/api, because the scheduler publishes these numbers
as Prometheus gauges. The scheduler runs without the API's auth configuration
(SORA_JWT_SECRET), and importing app.api.infra pulled in app.auth, which refuses
to import in production without that secret. This module must not import
app.api or app.auth. The route in app/api/infra.py calls coverage_report from
here, so the SQL that decides whether a day counts exists exactly once.
"""
from datetime import date, datetime, timedelta, timezone

from app.schemas import CoverageGap, ObservationCoverage




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


def coverage_window(days: int, not_before: date | None, today: date) -> tuple[date, date]:
    """The UTC days [lower, upper) that a coverage report examines.

    `upper` is today, excluded: today is incomplete by construction. `lower` is
    `days` back from today, or `not_before` if that is later. The rolling bound
    and the clock bound both apply, and whichever is later wins.
    """
    lower = today - timedelta(days=days)
    if not_before is not None and not_before > lower:
        lower = not_before
    return lower, today


def coverage_report(source: str = "openmeteo",
                    indicator: str = "temperature",
                    days: int = 30,
                    not_before: date | None = None,
                    today: date | None = None) -> ObservationCoverage:
    """The query behind the route, callable without a request.

    Separated so the scheduler can publish these numbers as metrics without
    carrying a second copy of the SQL. The endpoint's query is what decides
    whether a day counts, and it has already had to be corrected once -- from
    counting rows to counting hours. A copy would have kept the old answer
    while the endpoint served the new one, and nothing would have said so.

    `not_before` is an optional lower bound (the scheduler passes M3_CLOCK_START);
    left None, the window is the plain `days` back, as the route has always
    served. `today` exists so a test can state the calendar; production leaves
    it None and gets the UTC date now.
    """
    from sqlalchemy import text

    from app.database import SessionLocal
    from app.services.forecasting.entry_conditions import MIN_COVERAGE

    required = int(EXPECTED_PER_DAY * MIN_COVERAGE)
    if today is None:
        today = datetime.now(timezone.utc).date()
    lower, upper = coverage_window(days, not_before, today)

    db = SessionLocal()
    try:
        rows = db.execute(text(
            """
            -- DISTINCT hours, not rows (#179, amendment 1.3). The source is
            -- polled roughly hourly but stamped from Open-Meteo's `current`
            -- block, whose time moves at 15-minute resolution, so one hour can
            -- hold several rows -- every deployment adds one, since
            -- auto_openmeteo_ingestion runs at startup. Counting rows let a day
            -- covering 18 hours clear a threshold meant for 19 of 24, which is
            -- exactly the quiet dip this endpoint exists to surface. Measured
            -- on production 2026-09-19: three of the first sixteen days of the
            -- restarted clock were short in hours and complete in rows.
            --
            -- UTC days and UTC hours, whatever the session's TimeZone.
            -- `event_time` is timestamptz, so `event_time::date`,
            -- `date_trunc('hour', event_time)` and a zone-less cutoff are all
            -- read in the session's zone. Under a non-UTC session one complete
            -- UTC day became two short ones, and under a +05:30 zone each UTC
            -- hour spanned two local hours -- enough to hide an 18-hour day
            -- entirely. `event_time at time zone 'utc'` is the UTC wall clock;
            -- the edges are UTC midnights turned back into instants, bound as
            -- dates so the window is computed once, in Python, in UTC.
            SELECT to_char((event_time at time zone 'utc')::date, 'YYYY-MM-DD') AS day,
                   region_id,
                   count(DISTINCT date_trunc('hour', event_time at time zone 'utc'))
                       AS observations
              FROM environmental_observations
             WHERE source = :source
               AND indicator = :indicator
               AND temporal_kind = 'observed'
               AND event_time IS NOT NULL
               AND event_time >= CAST(:lower AS date)::timestamp at time zone 'utc'
               AND event_time <  CAST(:upper AS date)::timestamp at time zone 'utc'
             GROUP BY 1, 2
             ORDER BY 1 DESC, 2
            """
        ), {"source": source, "indicator": indicator,
            "lower": lower, "upper": upper}).all()

        gaps = [
            CoverageGap(day=day, region_id=region, indicator=indicator,
                        observations=int(count), required=required)
            for day, region, count in rows if int(count) < required
        ]
        return ObservationCoverage(
            source=source,
            indicator=indicator,
            required_per_day=required,
            days_examined=len({r[0] for r in rows}),
            points_examined=len({r[1] for r in rows}),
            complete_days=len(rows) - len(gaps),
            gap_count=len(gaps),
            gaps=gaps,
        )
    finally:
        db.close()
