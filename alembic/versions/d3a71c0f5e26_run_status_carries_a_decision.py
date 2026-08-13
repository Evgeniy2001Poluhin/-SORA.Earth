"""ingester_runs: a reason code, an age, and what to do about it

#74. `status` says how a run ended. `degraded` is a display: reading it, an
operator still cannot tell whether to wait, retry, page someone, or ignore it.

Three columns, all derived by app/ingesters/classification.py and none set by
hand:

    reason_code          closed vocabulary; `failure_reason` stays as prose
    source_age_seconds   how old the newest signal was when the run finished
    required_action      none | wait | investigate | escalate

The pair that motivates it: an empty source whose data is twenty minutes old is
`wait`; an empty source whose data is nine years old is `escalate`. Both are
`degraded` today, and the second ran for 333 consecutive runs looking exactly
like the first (#57).

Existing rows are left NULL rather than backfilled with a guess. A run recorded
before these columns existed has no measured age, and inventing one would put a
decision in the record that nobody made -- which is the defect this closes.

Revision ID: d3a71c0f5e26
Revises: b8f1d2c73a05
Create Date: 2026-08-13
"""
from alembic import op
import sqlalchemy as sa


revision = "d3a71c0f5e26"
down_revision = "b8f1d2c73a05"
branch_labels = None
depends_on = None

TABLE = "ingester_runs"
INDEX = "ix_ingester_runs_required_action"

COLUMNS = (
    ("reason_code", sa.Text()),
    ("required_action", sa.Text()),
    ("source_age_seconds", sa.Float()),
)


def _existing(conn):
    return {
        r[0] for r in conn.execute(sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :t"), {"t": TABLE})
    }


def upgrade() -> None:
    conn = op.get_bind()

    present = conn.execute(sa.text(
        "SELECT to_regclass(:t)"), {"t": TABLE}).scalar()
    if present is None:
        raise RuntimeError(
            f"{TABLE} does not exist; this migration alters it and will not "
            f"create it -- the schema is not where this revision expects it"
        )

    existing = _existing(conn)
    for name, type_ in COLUMNS:
        if name not in existing:
            op.add_column(TABLE, sa.Column(name, type_, nullable=True))

    op.create_index(INDEX, TABLE, ["required_action", sa.text("started_at DESC")])


def downgrade() -> None:
    op.drop_index(INDEX, table_name=TABLE)
    for name, _type in reversed(COLUMNS):
        op.drop_column(TABLE, name)
