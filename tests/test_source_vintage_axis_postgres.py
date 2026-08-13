"""Freshness is measured from the axis each `temporal_kind` actually has.

The version this replaces used `COALESCE(event_time, ingested_at)`. A `period`
row has no event_time by construction, so rosstat's 2024 snapshot was aged from
when it was last *written* -- and re-upserting the unchanged snapshot made it
"fresh today". That is the defect of #121 returning under a different name, in
the one field that decides what an operator is told to do.

    observed        event_time      a real observation instant
    period          period_end      the end of the period it covers
    not_applicable  no axis         the source has no time dimension at all
    legacy_*        excluded        its event_time is a stamped ingestion time,
                                    which is the thing #121 removed

Against PostgreSQL, because the query is PostgreSQL's: EXTRACT(EPOCH ...) and
an aggregate FILTER. Asserting it against SQLite would prove something about a
database nobody deploys.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.ingesters.runner import (
    VINTAGE_MEASURED,
    VINTAGE_NOT_APPLICABLE,
    VINTAGE_UNKNOWN,
    source_vintage,
)
from tests.postgres_scratch import (  # noqa: F401  (scratch_db is a fixture)
    requires_postgres,
    scratch_db,
)

DAY = 86400.0

# `source_revision` is required by the #121 CHECK for `period` and
# `not_applicable`: a row with no observation time has to say which snapshot it
# came from, or it is undedupable. The first version of this fixture omitted it
# and the constraint refused every insert -- correctly.
REVISION = "rev:v1:" + "a" * 64

INSERT = (
    "INSERT INTO environmental_observations "
    "  (region_id, indicator, value, source, source_record_id, temporal_kind, "
    "   event_time, period_start, period_end, source_revision, ingested_at, "
    "   created_at, updated_at, is_valid) "
    "VALUES (:region, :metric, 1.0, :source, :rid, :kind, "
    "        :event_time, :period_start, :period_end, :revision, now(), "
    "        now(), now(), true)"
)


@pytest.fixture
def db(scratch_db):
    """`source_vintage` reads through SessionLocal; point it at the scratch."""
    engine, _ = scratch_db
    import app.database as database

    original = database.SessionLocal
    database.SessionLocal = sessionmaker(bind=engine)
    try:
        yield engine
    finally:
        database.SessionLocal = original


def _add(engine, source, kind, *, rid, event_time=None,
         period_start=None, period_end=None):
    with engine.begin() as conn:
        conn.execute(text(INSERT), {
            "region": "RU-MOW", "metric": "m", "source": source, "rid": rid,
            "kind": kind, "event_time": event_time,
            "period_start": period_start, "period_end": period_end,
            # observed and legacy carry no revision; the other two must.
            "revision": None if kind in ("observed", "legacy_ingestion_time")
                        else REVISION,
        })


@requires_postgres
def test_an_observed_row_is_aged_from_its_event_time(db):
    _add(db, "openmeteo", "observed", rid="a",
         event_time=datetime.now(timezone.utc) - timedelta(days=3))

    age, basis = source_vintage("openmeteo")

    assert basis == VINTAGE_MEASURED
    assert age == pytest.approx(3 * DAY, rel=0.01)


@requires_postgres
def test_a_period_row_is_aged_from_its_period_end_not_its_write(db):
    """The case the whole file exists for.

    Written now, covering 2024. Aged from `ingested_at` it is seconds old; aged
    from `period_end` it is as old as the period it describes.
    """
    _add(db, "rosstat", "period", rid="b",
         period_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
         period_end=datetime(2024, 12, 31, tzinfo=timezone.utc))

    age, basis = source_vintage("rosstat")

    assert basis == VINTAGE_MEASURED
    assert age > 180 * DAY, (
        f"{age / DAY:.0f} days: a 2024 snapshot written today is being aged "
        f"from when it was written"
    )


@requires_postgres
def test_rewriting_an_unchanged_period_snapshot_does_not_refresh_it(db):
    """Idempotent re-ingestion must not move the vintage.

    #121 made an unchanged snapshot upsert rather than insert. If age came from
    `ingested_at`, every one of those runs would report the source as freshly
    current -- the exact conflation that let a dead source look healthy.
    """
    _add(db, "rosstat", "period", rid="c",
         period_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
         period_end=datetime(2024, 12, 31, tzinfo=timezone.utc))
    before, _ = source_vintage("rosstat")

    with db.begin() as conn:
        conn.execute(text(
            "UPDATE environmental_observations SET updated_at = now(), "
            "ingested_at = now() WHERE source_record_id = 'c'"))

    after, basis = source_vintage("rosstat")

    assert basis == VINTAGE_MEASURED
    assert after == pytest.approx(before, abs=5.0), (
        "rewriting the row changed its reported age"
    )


@requires_postgres
def test_a_source_of_only_not_applicable_rows_has_no_axis(db):
    """Distinct from "nobody measured": there is nothing to measure."""
    _add(db, "sber_veb_baseline", "not_applicable", rid="d")

    age, basis = source_vintage("sber_veb_baseline")

    assert basis == VINTAGE_NOT_APPLICABLE
    assert age is None


@requires_postgres
def test_a_source_with_nothing_stored_is_unknown_not_fresh(db):
    age, basis = source_vintage("never_seen")

    assert basis == VINTAGE_UNKNOWN
    assert age is None


@requires_postgres
def test_legacy_rows_do_not_supply_a_vintage(db):
    """Their event_time is the stamped ingestion time #121 removed.

    Counting it would report the 12,240 rows the migration reclassified as
    though someone had observed something the day they were written.
    """
    _add(db, "openaq", "legacy_ingestion_time", rid="e",
         event_time=datetime.now(timezone.utc) - timedelta(minutes=1))

    age, basis = source_vintage("openaq")

    assert basis == VINTAGE_UNKNOWN, (
        f"a legacy row supplied a vintage of {age}s; its event_time is an "
        f"ingestion stamp, not an observation"
    )


@requires_postgres
def test_a_mixed_source_takes_the_newest_row_that_has_an_axis(db):
    """One kind having no axis must not hide another kind that does."""
    now = datetime.now(timezone.utc)
    _add(db, "mixed", "not_applicable", rid="f")
    _add(db, "mixed", "observed", rid="g", event_time=now - timedelta(days=2))
    _add(db, "mixed", "period", rid="h",
         period_start=now - timedelta(days=400), period_end=now - timedelta(days=30))

    age, basis = source_vintage("mixed")

    assert basis == VINTAGE_MEASURED
    assert age == pytest.approx(2 * DAY, rel=0.01), (
        "the newest datable row is the observed one, two days old"
    )


@requires_postgres
def test_legacy_rows_do_not_hide_a_not_applicable_source(db):
    """The state has to fire on the source it was introduced for.

    Measured on production: sber_veb_baseline holds 85 `not_applicable` rows
    beside 2040 `legacy_ingestion_time` ones. Counting legacy in the
    denominator made `na == total` false, so the source reported `unknown` and
    every clean run of it asked for an investigation -- the third state existed
    and never once applied.
    """
    _add(db, "sber_veb_baseline", "not_applicable", rid="i")
    for n in range(3):
        _add(db, "sber_veb_baseline", "legacy_ingestion_time", rid=f"legacy{n}",
             event_time=datetime.now(timezone.utc) - timedelta(days=n + 1))

    age, basis = source_vintage("sber_veb_baseline")

    assert basis == VINTAGE_NOT_APPLICABLE, (
        f"basis={basis}: legacy rows are hiding the fact that every datable "
        f"row of this source carries no date"
    )
    assert age is None


# --- the tolerance is a separate contract from the polling interval ----------


@requires_postgres
def test_rosstat_2024_is_not_stale_without_a_declared_tolerance(db):
    """The production case that stopped this being merged.

    rosstat polls every 180 days (`default_ttl_hours = 24 * 180`) and publishes
    annually, so its newest observation is 590 days old -- measured on
    production, 2026-08 against a period ending 2024-12-31. Treating the polling
    interval as a vintage tolerance made every clean run escalate, forever, over
    a snapshot that is exactly as current as rosstat has published.

    No `max_vintage_hours` is declared for it, so the age takes no part in the
    verdict.
    """
    from app.ingesters.classification import (
        NOT_CONFIGURED, REASON_OK, classify_run, freshness_status,
        required_action,
    )
    from app.ingesters.rosstat import RosstatIngester

    _add(db, "rosstat", "period", rid="v1",
         period_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
         period_end=datetime(2024, 12, 31, tzinfo=timezone.utc))

    vintage, basis = source_vintage("rosstat")
    assert basis == VINTAGE_MEASURED and vintage > 400 * DAY

    ingester = RosstatIngester()
    assert getattr(ingester, "max_vintage_hours", None) is None, (
        "a tolerance has been declared for rosstat; this test is about the "
        "case where none has, and the number must not be guessed"
    )
    # The polling interval exists and is deliberately not used here.
    assert ingester.default_ttl_hours == 24 * 180

    status = freshness_status(vintage, max_vintage_seconds=None)
    assert status == NOT_CONFIGURED

    clean = classify_run(received=425, accepted=425, rejected=0)
    assert clean.reason_code == REASON_OK
    assert required_action(clean, freshness=status) == "none", (
        "a clean run over the newest data rosstat has published is not an "
        "escalation"
    )


@requires_postgres
def test_the_same_rosstat_data_is_stale_once_a_tolerance_is_declared(db):
    """And the mechanism still works when someone does agree a number.

    The point is not that rosstat is never stale -- it is that nobody has said
    when. Given a contract, the same rows produce the same verdict the polling
    interval was wrongly producing on its own.
    """
    from app.ingesters.classification import (
        STALE, classify_run, freshness_status, required_action,
    )

    _add(db, "rosstat", "period", rid="v2",
         period_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
         period_end=datetime(2024, 12, 31, tzinfo=timezone.utc))

    vintage, _ = source_vintage("rosstat")
    status = freshness_status(vintage, max_vintage_seconds=400 * DAY)

    assert status == STALE
    clean = classify_run(received=425, accepted=425, rejected=0)
    assert required_action(clean, freshness=status) == "escalate"
