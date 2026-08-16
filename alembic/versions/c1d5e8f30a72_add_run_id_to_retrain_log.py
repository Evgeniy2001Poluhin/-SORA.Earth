"""Add run_id to retrain_log: one physical retrain, however many rows it writes.

#199 phase 2A.

A closed-loop cycle writes two rows -- `_do_retrain` records the training and
the loop records the decision -- and nothing related them but a timestamp.
Measured on a real cycle 2026-08-16:

    id=1 job='model_retrain'  status='success'   registry_ok=False
    id=2 job='closed_loop'    status='rejected'  model_version=None

`retrain_models` produced two rows both saying `success`, so `admin_ai.py`
counted one physical run twice.

**Nullable, and not backfilled.** Every row written before this column existed
belongs to a run whose grouping was never recorded, so it cannot be recovered.
Inventing one -- pairing rows by proximity in time, say -- would put a
reconstruction into the same field as a fact, and nothing downstream could tell
them apart afterwards. NULL means "this predates the column", which readers
handle by counting the row as its own run: exactly what they already did.

Revision ID: c1d5e8f30a72
Revises: b7d419e2c8a1
"""
from alembic import op


revision = "c1d5e8f30a72"
down_revision = "b7d419e2c8a1"
branch_labels = None
depends_on = None


def upgrade():
    # IF NOT EXISTS, following cb5a035aed2f, because there are two ways a
    # deployment reaches this point and both must work.
    #
    # `tests/test_schema_is_migrated.py::test_both_paths_to_head_produce_the_same_schema`
    # builds the converged path by stopping at e3f8a7c15d92, letting
    # `create_all()` build the tables the way production actually got them, and
    # then running the remaining revisions over that. `create_all()` reads the
    # model, which now declares `run_id`, so a plain ADD COLUMN fails with
    # DuplicateColumn on that path -- which is exactly how this was caught.
    op.execute("ALTER TABLE retrain_log ADD COLUMN IF NOT EXISTS run_id VARCHAR(36)")
    # Indexed because the questions this column exists to answer are all of the
    # form "the other rows of this run", which is a lookup by value.
    op.execute("CREATE INDEX IF NOT EXISTS ix_retrain_log_run_id ON retrain_log (run_id)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_retrain_log_run_id")
    op.execute("ALTER TABLE retrain_log DROP COLUMN IF EXISTS run_id")
