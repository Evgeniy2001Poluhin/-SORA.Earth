"""What the migration guarantees about a period, independently of who writes it.

These are the database's own refusals, so they hold for the backfill, for the
ingester, and for anyone with a psql prompt. The write path is tested separately
and by running it -- see test_backfill_integration_postgres.py, which exists
because an earlier version of this file claimed to test the backfill while never
invoking it.

The pairing constraint is the one that matters. Without it a row can say
`ambiguous` and carry a date, or say `recovered_inferred` and carry none; both
are storable, and both are read downstream as facts.
"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.postgres_scratch import (  # noqa: F401  (scratch_db is a fixture)
    requires_postgres,
    scratch_db,
)

INSERT = (
    "INSERT INTO country_indicator_history "
    "  (country_iso3, indicator_code, source, value, fetched_at, "
    "   as_of_date, period_status) "
    "VALUES ('SAU', 'NY.GDP.PCAP.CD', 'world_bank', 34536.66, now(), "
    "        :as_of, :status)"
)

STATUSES = [
    "recovered_inferred",
    "ambiguous",
    "no_match_current_vintage",
    "outside_query_window",
    "source_unavailable",
    "period_not_applicable",
    "unchecked",
]


def insert(engine, status, as_of):
    with engine.begin() as conn:
        conn.execute(text(INSERT), {"status": status, "as_of": as_of})


@requires_postgres
def test_the_migration_added_its_columns_and_constraints(scratch_db):
    """Asserted before any scenario runs.

    The harness this replaced sent `alembic upgrade head` to /dev/null and
    checked nothing. When the job turned out to have no alembic installed, the
    table did not exist, and ten assertions failed for a reason that had nothing
    to do with what they were testing.
    """
    engine, _ = scratch_db
    with engine.begin() as conn:
        columns = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT attname FROM pg_attribute "
                    "WHERE attrelid = 'country_indicator_history'::regclass "
                    "  AND attnum > 0 AND NOT attisdropped"
                )
            )
        }
        constraints = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'country_indicator_history'::regclass "
                    "  AND contype = 'c'"
                )
            )
        }

    assert {
        "period_status",
        "period_run_id",
        "period_method",
        "period_rule_version",
        "period_candidates",
        "period_source_vintage",
        "period_response_sha256",
        "period_resolved_at",
    } <= columns
    assert {"ck_cih_period_status", "ck_cih_period_date_agrees"} <= constraints


@requires_postgres
@pytest.mark.parametrize("status", STATUSES)
def test_every_status_in_the_vocabulary_is_storable(scratch_db, status):
    """The whole set, not a sample of it.

    A CHECK listing the statuses is only as good as the list, and a typo in the
    migration would make one status permanently unwritable -- discovered later,
    by the run that needed it.
    """
    engine, _ = scratch_db
    as_of = "2025-01-01" if status == "recovered_inferred" else None
    insert(engine, status, as_of)


@requires_postgres
def test_a_status_nobody_defined_is_refused(scratch_db):
    """Free TEXT admits a typo as a new state, and a state nobody defined reads
    as data rather than as a mistake.

    No date, deliberately. Written with one, `recovered` is refused by
    ck_cih_period_date_agrees before the vocabulary is ever consulted -- the row
    is rejected either way, and the test passes while proving nothing about the
    constraint it names.
    """
    engine, _ = scratch_db
    with pytest.raises(IntegrityError, match="ck_cih_period_status"):
        insert(engine, "recovered", None)


@requires_postgres
def test_a_recovered_verdict_without_a_date_is_refused(scratch_db):
    engine, _ = scratch_db
    with pytest.raises(IntegrityError, match="ck_cih_period_date_agrees"):
        insert(engine, "recovered_inferred", None)


@requires_postgres
@pytest.mark.parametrize("status", [s for s in STATUSES if s != "recovered_inferred"])
def test_no_other_verdict_may_carry_a_date(scratch_db, status):
    """Enumerated over every status that is not `recovered_inferred`, because
    each one of them says, in its own words, that no year was established."""
    engine, _ = scratch_db
    with pytest.raises(IntegrityError, match="ck_cih_period_date_agrees"):
        insert(engine, status, "2025-01-01")


@requires_postgres
def test_a_dated_row_with_no_verdict_is_left_alone(scratch_db):
    """The rows the ingester dates itself, post-#59: a date and no verdict.

    Nothing inferred them, so no verdict is the correct state -- and a
    constraint that forbade it would refuse every row written from now on.
    """
    engine, _ = scratch_db
    insert(engine, None, "2025-01-01")


@requires_postgres
def test_the_index_a_full_pass_depends_on_exists(scratch_db):
    """"Which rows still have no verdict" is asked on every pass, over ninety
    thousand rows; "what did that run do" is how a bad run is undone."""
    engine, _ = scratch_db
    with engine.begin() as conn:
        indexes = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT ic.relname FROM pg_index i "
                    "  JOIN pg_class ic ON ic.oid = i.indexrelid "
                    " WHERE i.indrelid = 'country_indicator_history'::regclass"
                )
            )
        }
    assert {"ix_cih_period_status", "ix_cih_period_run"} <= indexes
