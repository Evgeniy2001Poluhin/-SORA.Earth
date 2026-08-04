"""record why an indicator period is absent

`country_indicator_history.as_of_date` is NULL in 90,403 of 93,447 rows. The
NULL carries two entirely different facts and nothing distinguishes them:

  58,657 rows from world_bank    -- the source stated a year and the ingester
                                    discarded it (#58, fixed for new rows in #59)
  31,746 rows from benchmark and
         global_avg              -- the source states no period at all, and
                                    never did

A third case appears the moment a backfill runs: rows whose period the source
once stated and can no longer be recovered, because the figures have been
revised since and the stored value matches no year the API still returns.

Reading a NULL, nobody can tell "we lost it", "there was never one" and "it is
gone for good" apart -- and the three call for different responses. The first is
a defect to fix, the second is a property of the source to document, the third is
a loss to record so nobody spends an afternoon rediscovering it.

    period_status
      stated         as_of_date holds the period the source gave
      source_has_none  the source publishes no period for this value
      unrecoverable  a period existed and cannot now be established
      NULL           not yet examined

NULL remains meaningful on purpose: until the backfill has looked at a row, the
honest answer is that nobody has checked. Filling every row with a guess at
migration time would be the same error this whole issue is about, committed
against 90,000 rows at once.

Revision ID: e7b3c9d15f04
Revises: c4d1f8a26b93
Create Date: 2026-08-04
"""
from alembic import op
import sqlalchemy as sa

revision = "e7b3c9d15f04"
down_revision = "c4d1f8a26b93"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE country_indicator_history "
            "ADD COLUMN IF NOT EXISTS period_status TEXT"
        )
    )
    # Records which run established a row's status, so a backfill that goes
    # wrong can be told from one that went right, and re-run against only its
    # own work.
    op.execute(
        sa.text(
            "ALTER TABLE country_indicator_history "
            "ADD COLUMN IF NOT EXISTS period_source TEXT"
        )
    )
    # "Which rows still have no verdict" is the question a backfill asks on
    # every pass, over ninety thousand rows.
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_cih_period_status "
            "ON country_indicator_history (period_status) "
            "WHERE period_status IS NULL"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_cih_period_status"))
    op.execute(
        sa.text(
            "ALTER TABLE country_indicator_history "
            "DROP COLUMN IF EXISTS period_source"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE country_indicator_history "
            "DROP COLUMN IF EXISTS period_status"
        )
    )
