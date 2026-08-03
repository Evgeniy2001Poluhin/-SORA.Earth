"""classify ingester runs beyond ok/error

`ingester_runs.status` held 'ok' whenever no exception escaped. Measured on
production 2026-08-03: openaq had 62 runs, all 'ok', all writing zero rows,
while rosstat and sber_veb_baseline wrote 29325 and 5865. One word could not
tell "worked" from "reachable, authenticated and empty".

The columns added here separate the questions that were collapsed:

  execution_status       completed | failed        -- did the code finish
  primary_source_status  data | empty | unavailable | rejected
  data_outcome           primary | fallback | partial | none
  records_received / _accepted / _rejected
  failure_reason         why anything short of success ended that way

Nullable and without a default. Backfilling the 200-odd existing rows would mean
inventing verdicts for runs nobody classified, and a guessed 'success' is
exactly the failure being corrected. They stay NULL, which reads as "not
classified" and is the truth.

Revision ID: c4d1f8a26b93
Revises: f2c9a1d47b30
Create Date: 2026-08-03
"""
from alembic import op
import sqlalchemy as sa

revision = "c4d1f8a26b93"
down_revision = "f2c9a1d47b30"
branch_labels = None
depends_on = None


COLUMNS = [
    ("execution_status", sa.Text()),
    ("primary_source_status", sa.Text()),
    ("data_outcome", sa.Text()),
    ("records_received", sa.Integer()),
    ("records_accepted", sa.Integer()),
    ("records_rejected", sa.Integer()),
    ("failure_reason", sa.Text()),
]


def upgrade() -> None:
    # The table itself first.
    #
    # ingester_runs is created by migrations/004_esg_pipeline.sql, a raw SQL file
    # outside the alembic chain, so on a database built from migrations alone it
    # does not exist and the ALTERs below fail. Found by running this migration
    # against an empty PostgreSQL rather than trusting it -- the same gap
    # f2c9a1d47b30 was written for, in a table nobody had noticed was missing.
    #
    # The definition matches 004 exactly; where 004 has already run this is a
    # no-op.
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS ingester_runs (
                id           BIGSERIAL PRIMARY KEY,
                source       TEXT NOT NULL,
                started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                finished_at  TIMESTAMPTZ,
                status       TEXT,
                rows_written INT,
                error        TEXT
            )
            """
        )
    )

    # IF NOT EXISTS on each: this table was created outside the migration chain
    # (raw SQL in the ingester runner), so a database may already carry some of
    # these from an earlier hand-applied change. Matching on name alone is the
    # same shortcut f2c9a1d47b30 documents; it is safe here because every column
    # is new and nullable.
    for name, type_ in COLUMNS:
        op.execute(
            sa.text(
                f"ALTER TABLE ingester_runs ADD COLUMN IF NOT EXISTS "
                f"{name} {type_.compile(dialect=op.get_bind().dialect)}"
            )
        )

    # Reading "which sources are degraded right now" is the question this whole
    # change exists to answer, so it gets an index rather than a sequential scan
    # over every run ever recorded.
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_ingester_runs_source_status "
            "ON ingester_runs (source, status, started_at DESC)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_ingester_runs_source_status"))
    for name, _ in COLUMNS:
        op.execute(sa.text(f"ALTER TABLE ingester_runs DROP COLUMN IF EXISTS {name}"))
