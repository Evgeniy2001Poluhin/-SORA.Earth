"""link retrain runs to ordered immutable snapshots

Revision ID: a6c4e91d7b20
Revises: f4b7c2d91e06
"""
from alembic import op
import sqlalchemy as sa


revision = "a6c4e91d7b20"
down_revision = "f4b7c2d91e06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("retrain_log", sa.Column("snapshot_ids_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("retrain_log", "snapshot_ids_json")
