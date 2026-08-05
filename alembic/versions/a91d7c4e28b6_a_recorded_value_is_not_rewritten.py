"""a recorded value is not rewritten

Issue #75. Point-in-time reads resolve a value by `fetched_at`, so the answer
for a past date is only stable while `value` and `fetched_at` stay as written.
Measured before adding this: they already do -- across 95,024 production rows
there is exactly one distinct value per (country, as_of_date), and no code path
rewrites either column. The property holds by accident of how the writer
happens to be written, and nothing would report it breaking.

What this constrains, and what it deliberately does not:

    forbidden   changing `value` or `fetched_at` on an existing row
    allowed     correcting `as_of_date` and the period_* provenance columns
    allowed     DELETE

The period columns stay writable because correcting them is a real operation
that has already run: the #58 backfill assigned a period to 45,065 rows on
2026-08-05. Blocking that would forbid the one maintenance path this table has.

DELETE stays allowed on purpose. The table grows by roughly 1,700 rows a day
and 99.7% of it is redundant re-fetches; a trigger that forbids deletion would
turn a future pruning job into a migration, and nothing in the point-in-time
read depends on old rows being unremovable -- only on the surviving ones being
unaltered.

The index supports the read itself: (country_iso3, indicator_code, fetched_at
DESC) is exactly the lookup, and the existing single-column indexes make the
planner filter then sort.

Revision ID: a91d7c4e28b6
Revises: e7b3c9d15f04
Create Date: 2026-08-05

"""
import sqlalchemy as sa
from alembic import op

revision = "a91d7c4e28b6"
down_revision = "e7b3c9d15f04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_cih_point_in_time "
            "ON country_indicator_history "
            "(country_iso3, indicator_code, fetched_at DESC)"
        )
    )

    # IS DISTINCT FROM rather than <>: a NULL on either side makes <> return
    # NULL, the IF is not taken, and the change goes through unreported --
    # which is the case worth catching, since `value` is nullable.
    op.execute(
        sa.text(
            """
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
        )
    )

    op.execute(
        sa.text("DROP TRIGGER IF EXISTS trg_cih_value_written_once "
                "ON country_indicator_history")
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_cih_value_written_once "
            "BEFORE UPDATE ON country_indicator_history "
            "FOR EACH ROW EXECUTE FUNCTION cih_refuse_value_rewrite()"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DROP TRIGGER IF EXISTS trg_cih_value_written_once "
                "ON country_indicator_history")
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS cih_refuse_value_rewrite()"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_cih_point_in_time"))
