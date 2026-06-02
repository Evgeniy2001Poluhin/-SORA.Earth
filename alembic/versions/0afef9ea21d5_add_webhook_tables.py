"""add webhook tables

Revision ID: 0afef9ea21d5
Revises: 963bec0d1503
Create Date: 2026-06-02 13:54:39.997637

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0afef9ea21d5'
down_revision = '963bec0d1503'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('webhook_subscriptions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('url', sa.String(512), nullable=False),
        sa.Column('event_type', sa.String(64), nullable=False, server_default='drift'),
        sa.Column('secret', sa.String(128), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_table('webhook_deliveries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('subscription_id', sa.Integer(), index=True),
        sa.Column('event_type', sa.String(64)),
        sa.Column('status_code', sa.Integer()),
        sa.Column('ok', sa.Boolean(), server_default=sa.false()),
        sa.Column('error', sa.String(512)),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )

def downgrade() -> None:
    op.drop_table('webhook_deliveries')
    op.drop_table('webhook_subscriptions')
