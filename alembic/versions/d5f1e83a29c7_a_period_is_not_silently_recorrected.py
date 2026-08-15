"""a period is not silently re-corrected

Issue #164. `value` and `fetched_at` have been written-once since a91d7c4e28b6;
`as_of_date` was deliberately left mutable, because the #58 backfill had to
assign periods to 45,065 rows and forbidding that would have forbidden the
table's one maintenance path.

That exception was correct when it was written. What changed is a measurement,
not a preference.

Measured on production 2026-08-14: `period_run_id` holds **one** distinct value
across 104,309 rows. Period correction happened once, in a single run on
2026-08-05, and not since. It is an event, not a process -- so building a
versioning mechanism for it would be building for a hypothetical, and leaving
it open costs something real:

    value, fetched_at   cannot be rewritten, so a point-in-time read is exact
    as_of_date          could change under it, silently, and the row keeps no
                        record of what it said before

So which period a value was attributed to is not reproducible, while the value
itself is. A backtest could see a point move from one period to another with
nothing anywhere saying it had.

Refused from here. A future correction remains possible and becomes a decision
that drops this guard -- something a person reviews -- rather than an UPDATE
nobody sees.

Only the three columns a point-in-time read resolves on are frozen. The
provenance columns (`period_status`, `period_method`, `period_run_id` and the
rest) stay writable: they describe *how* a period was established, and freezing
them would forbid recording that a row was reviewed.

`CREATE OR REPLACE FUNCTION`, so the trigger created by a91d7c4e28b6 is
untouched and this is idempotent on both paths to head.

Revision ID: d5f1e83a29c7
Revises: b7d419e2c8a1
Create Date: 2026-08-15

"""
import sqlalchemy as sa
from alembic import op

revision = "d5f1e83a29c7"
down_revision = "b7d419e2c8a1"
branch_labels = None
depends_on = None


_WITH_PERIOD = """
CREATE OR REPLACE FUNCTION cih_refuse_value_rewrite()
RETURNS trigger AS $$
BEGIN
    IF NEW.value IS DISTINCT FROM OLD.value THEN
        RAISE EXCEPTION
            'country_indicator_history.value is written once '
            '(row id=%, was %, attempted %). Point-in-time reads '
            'resolve by fetched_at and would silently change '
            'answers for past dates.',
            OLD.id, OLD.value, NEW.value;
    END IF;
    IF NEW.fetched_at IS DISTINCT FROM OLD.fetched_at THEN
        RAISE EXCEPTION
            'country_indicator_history.fetched_at is written once '
            '(row id=%, was %, attempted %). It is the vintage a '
            'point-in-time read resolves on.',
            OLD.id, OLD.fetched_at, NEW.fetched_at;
    END IF;
    IF NEW.as_of_date IS DISTINCT FROM OLD.as_of_date THEN
        RAISE EXCEPTION
            'country_indicator_history.as_of_date is written once '
            '(row id=%, was %, attempted %). Correcting it in place '
            'moves a value from one period to another with no record '
            'that it moved (#164).',
            OLD.id, OLD.as_of_date, NEW.as_of_date;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

#: The body as a91d7c4e28b6 left it, for the downgrade. Restated rather than
#: re-run from that file: a migration that reaches into another one's SQL breaks
#: when that one is edited, and this is the only place the older shape matters.
_WITHOUT_PERIOD = """
CREATE OR REPLACE FUNCTION cih_refuse_value_rewrite()
RETURNS trigger AS $$
BEGIN
    IF NEW.value IS DISTINCT FROM OLD.value THEN
        RAISE EXCEPTION
            'country_indicator_history.value is written once '
            '(row id=%, was %, attempted %). Point-in-time reads '
            'resolve by fetched_at and would silently change '
            'answers for past dates.',
            OLD.id, OLD.value, NEW.value;
    END IF;
    IF NEW.fetched_at IS DISTINCT FROM OLD.fetched_at THEN
        RAISE EXCEPTION
            'country_indicator_history.fetched_at is written once '
            '(row id=%, was %, attempted %). It is the vintage a '
            'point-in-time read resolves on.',
            OLD.id, OLD.fetched_at, NEW.fetched_at;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    op.execute(sa.text(_WITH_PERIOD))


def downgrade() -> None:
    op.execute(sa.text(_WITHOUT_PERIOD))
