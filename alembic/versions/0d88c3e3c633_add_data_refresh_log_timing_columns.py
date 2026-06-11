"""add data_refresh_log timing columns

Revision ID: 0d88c3e3c633
Revises: a1b2c3d4e5f6
Create Date: 2026-06-11 17:14:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0d88c3e3c633"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None

_COLS = [
    ("started_at",     sa.DateTime(timezone=True)),
    ("finished_at",    sa.DateTime(timezone=True)),
    ("duration_sec",   sa.Float()),
    ("trigger_source", sa.String()),
]

def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("data_refresh_log")}
    for name, type_ in _COLS:
        if name not in existing:
            op.add_column("data_refresh_log", sa.Column(name, type_, nullable=True))

def downgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("data_refresh_log")}
    for name, _ in reversed(_COLS):
        if name in existing:
            op.drop_column("data_refresh_log", name)
