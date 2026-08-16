"""Split retrain_log.status into the stages it was conflating.

#199 phase 2B.

One `status` had to answer three different questions, so it answered none of
them. Measured 2026-08-16 on a real cycle: a row read `success` while its own
`metrics_json` held `registry_ok=False` -- the training succeeded, the
registration did not, and the field had no way to say both.

Four columns, all nullable:

- `training_status`   -- did the training finish
- `registry_status`   -- did the model reach the registry
- `promotion_status`  -- what the gate decided
- `failure_reason`    -- why, when one of them is not a success

`status` stays and keeps its current meaning, computed by `project_status` in
`app/database.py` -- one definition, because two implementations of "what does
this mean now" is how `closed_loop_retrain` and `app/api/infra.py` came to have
two different promotion gates. Twelve readers filter on `status` today (eight
backend modules, four frontend components), so it cannot be removed here;
dropping it is a separate phase, after each reader has moved and the same
enumeration confirms nothing is left.

IF NOT EXISTS on every column, following cb5a035aed2f and c1d5e8f30a72. There
are two ways a deployment reaches head, and the converged one lets
`create_all()` build the table from the model first -- which now declares these,
so a plain ADD COLUMN fails there. That is not hypothetical: it is how the
previous revision was caught in CI.

Revision ID: d2a7f4b81c65
Revises: c1d5e8f30a72
"""
from alembic import op


revision = "d2a7f4b81c65"
down_revision = "c1d5e8f30a72"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("training_status", "VARCHAR(30)"),
    ("registry_status", "VARCHAR(30)"),
    ("promotion_status", "VARCHAR(30)"),
    ("failure_reason", "TEXT"),
)


def upgrade():
    for name, sql_type in _COLUMNS:
        op.execute(f"ALTER TABLE retrain_log ADD COLUMN IF NOT EXISTS {name} {sql_type}")
    # The three status columns are indexed because every reader that moves off
    # `status` will filter on one of them. `failure_reason` is prose and is not.
    for name, _ in _COLUMNS[:3]:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_retrain_log_{name} ON retrain_log ({name})"
        )


def downgrade():
    for name, _ in _COLUMNS[:3]:
        op.execute(f"DROP INDEX IF EXISTS ix_retrain_log_{name}")
    for name, _ in _COLUMNS:
        op.execute(f"ALTER TABLE retrain_log DROP COLUMN IF EXISTS {name}")
