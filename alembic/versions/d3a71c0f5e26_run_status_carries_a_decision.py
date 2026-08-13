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

    # Checked, like the columns above. The index is also declared in the model's
    # __table_args__, so on a database where `Base.metadata.create_all()` built
    # the tables -- the converged path production actually took, and the one
    # tests/test_schema_is_migrated.py exercises -- it already exists, and an
    # unconditional CREATE INDEX fails the whole revision with DuplicateTable.
    #
    # Half-idempotent is the worst of both: it works on a fresh install, where
    # nobody notices, and refuses on the deployment shape that already has data.
    if not _index_exists(conn):
        op.create_index(INDEX, TABLE, ["required_action", sa.text("started_at DESC")])


def _index_exists(conn):
    return conn.execute(sa.text(
        "SELECT 1 FROM pg_indexes WHERE tablename = :t AND indexname = :i"),
        {"t": TABLE, "i": INDEX}).scalar() is not None


def downgrade() -> None:
    conn = op.get_bind()

    # Same reasoning in reverse: the index may have been created by the models
    # rather than by this revision, and dropping what is not there is an error
    # rather than a no-op.
    if _index_exists(conn):
        op.drop_index(INDEX, table_name=TABLE)

    existing = _existing(conn)
    for name, _type in reversed(COLUMNS):
        if name in existing:
            op.drop_column(TABLE, name)
