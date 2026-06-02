"""add health_pings table

Revision ID: 31e5cc432377
Revises: 0afef9ea21d5
Create Date: 2026-06-02 14:11:06.798947

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '31e5cc432377'
down_revision = '0afef9ea21d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('health_pings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ts', sa.DateTime(), nullable=True),
        sa.Column('component', sa.String(32)),
        sa.Column('ok', sa.Boolean(), server_default=sa.true()),
    )
    op.create_index('ix_health_pings_ts', 'health_pings', ['ts'])
    op.create_index('ix_health_pings_component', 'health_pings', ['component'])

def downgrade() -> None:
    op.drop_table('health_pings')
