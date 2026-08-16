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
import sqlalchemy as sa


revision = "c1d5e8f30a72"
down_revision = "b7d419e2c8a1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("retrain_log", sa.Column("run_id", sa.String(length=36), nullable=True))
    # Indexed because the questions this column exists to answer are all of the
    # form "the other rows of this run", which is a lookup by value.
    op.create_index("ix_retrain_log_run_id", "retrain_log", ["run_id"])


def downgrade():
    op.drop_index("ix_retrain_log_run_id", table_name="retrain_log")
    op.drop_column("retrain_log", "run_id")
