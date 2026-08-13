"""What `_audit_finish` actually stores, read back from the database.

The mutation that survived: writing NULL for `max_vintage_seconds`. Every test
of the attention view filled that column in by hand and then asserted the API
returned it, so the write path had no coverage at all -- the API was being
tested against fixtures, not against the runner.

Against PostgreSQL because ingester_runs is written with raw SQL through
SessionLocal, and the columns are the migration's.
"""
import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.ingesters import runner
from app.ingesters.classification import classify_run
from tests.postgres_scratch import (  # noqa: F401  (scratch_db is a fixture)
    requires_postgres,
    scratch_db,
)

DAY = 86400.0


@pytest.fixture
def db(scratch_db):
    engine, _ = scratch_db
    import app.database as database

    original = database.SessionLocal
    database.SessionLocal = sessionmaker(bind=engine)
    try:
        yield engine
    finally:
        database.SessionLocal = original


def _row(engine, rid):
    with engine.begin() as conn:
        return conn.execute(text(
            "SELECT required_action, freshness_status, source_vintage_seconds, "
            "       max_vintage_seconds, reason_code, status "
            "FROM ingester_runs WHERE id = :i"), {"i": rid}).one()


@requires_postgres
def test_every_input_to_the_verdict_is_stored(db):
    """Not just the verdict.

    A threshold can be changed after a run. A row holding `escalate` and a
    vintage of 590 days, with no record of the tolerance in force, cannot be
    re-read a month later: nobody can tell whether the data was old or the
    number moved.
    """
    rid = runner._audit_start("openaq")
    assert rid is not None

    runner._audit_finish(
        rid, classify_run(received=0, accepted=0, rejected=0),
        action="escalate", vintage_seconds=9 * 365 * DAY,
        freshness="stale", max_vintage_seconds=30 * DAY,
    )

    row = _row(db, rid)
    assert row.required_action == "escalate"
    assert row.freshness_status == "stale"
    assert row.source_vintage_seconds == pytest.approx(9 * 365 * DAY)
    assert row.max_vintage_seconds == pytest.approx(30 * DAY), (
        "the tolerance the decision was made against was not recorded"
    )
    assert row.reason_code == "source_empty"


@requires_postgres
def test_a_run_with_no_declared_tolerance_records_that_too(db):
    """`not_configured` beside a NULL threshold is a fact worth storing.

    It is what stops a later reader taking `none` for proven freshness.
    """
    rid = runner._audit_start("rosstat")

    runner._audit_finish(
        rid, classify_run(received=425, accepted=425, rejected=0),
        action="none", vintage_seconds=590 * DAY,
        freshness="not_configured", max_vintage_seconds=None,
    )

    row = _row(db, rid)
    assert row.required_action == "none"
    assert row.freshness_status == "not_configured"
    assert row.source_vintage_seconds == pytest.approx(590 * DAY)
    assert row.max_vintage_seconds is None
    assert row.status == "success"
