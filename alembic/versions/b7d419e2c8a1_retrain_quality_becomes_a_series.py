"""retrain quality becomes a series, not a blob

The columns already had values: `metrics_json` has carried `roc_auc`,
`test_samples`, `train_samples`, `accuracy` and `f1_score` since the table
existed. What was missing is any way to ask across runs -- has AUC moved, on
how many test rows, is 0.81 against 0.80 inside the noise. A JSON string
answers none of those, so the promotion gate compares two point estimates with
no way to know whether the difference is real.

The backfill reads what is there. Measured on production before writing this:
21 rows, every one carrying `roc_auc` and `test_samples`, all from 2026-07-05
-- one day, forty days ago. That is the second finding and it is not fixed
here: a threshold that has not been reached because no retrain was attempted is
zero coverage, not a passed check.

Row by row inside an exception block: `metrics_json` is `Text` and nothing has
ever guaranteed every value parses.

Idempotent: `roc_auc IS NULL` excludes anything a previous run filled.

Revision ID: b7d419e2c8a1
Revises: c4e2a7b91f38
Create Date: 2026-08-14

"""
import sqlalchemy as sa
from alembic import op

revision = "b7d419e2c8a1"
down_revision = "c4e2a7b91f38"
branch_labels = None
depends_on = None

COLUMNS = (
    ("roc_auc", sa.Float()),
    ("accuracy", sa.Float()),
    ("f1_score", sa.Float()),
    ("test_samples", sa.Integer()),
    ("train_samples", sa.Integer()),
)

BACKFILL = """
DO $$
DECLARE
    r      RECORD;
    m      jsonb;
    filled integer := 0;
BEGIN
    FOR r IN
        SELECT id, metrics_json FROM retrain_log
         WHERE roc_auc IS NULL AND metrics_json IS NOT NULL
    LOOP
        BEGIN
            m := r.metrics_json::jsonb;
        EXCEPTION WHEN others THEN
            CONTINUE;
        END;

        UPDATE retrain_log SET
            roc_auc       = NULLIF(m ->> 'roc_auc', '')::double precision,
            accuracy      = NULLIF(m ->> 'accuracy', '')::double precision,
            f1_score      = NULLIF(m ->> 'f1_score', '')::double precision,
            test_samples  = NULLIF(m ->> 'test_samples', '')::integer,
            train_samples = NULLIF(m ->> 'train_samples', '')::integer
         WHERE id = r.id;
        filled := filled + 1;
    END LOOP;

    RAISE NOTICE 'lifted metrics out of json for % retrain run(s)', filled;
END $$;
"""


def _existing() -> set:
    """Columns retrain_log already has.

    There are two paths to head: this migration chain, and `create_all()` from
    the model, which the test stand exercises to prove the two agree. On the
    second path the columns arrive with the table, and an unconditional
    `add_column` then raises DuplicateColumn -- the same shape that broke
    d3a71c0f5e26 on the converged path.
    """
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns("retrain_log")}


def _has_index(name: str) -> bool:
    bind = op.get_bind()
    return name in {i["name"] for i in sa.inspect(bind).get_indexes("retrain_log")}


def upgrade() -> None:
    present = _existing()
    for name, kind in COLUMNS:
        if name not in present:
            op.add_column("retrain_log", sa.Column(name, kind, nullable=True))
    if not _has_index("ix_retrain_log_roc_auc"):
        op.create_index("ix_retrain_log_roc_auc", "retrain_log", ["roc_auc"])
    op.execute(BACKFILL)


def downgrade() -> None:
    """The columns go; `metrics_json` was never emptied, so nothing is lost.

    That is the reason the blob is kept and still written rather than migrated
    away: it makes this reversible without a second backfill.
    """
    if _has_index("ix_retrain_log_roc_auc"):
        op.drop_index("ix_retrain_log_roc_auc", table_name="retrain_log")
    present = _existing()
    for name, _kind in reversed(COLUMNS):
        if name in present:
            op.drop_column("retrain_log", name)
