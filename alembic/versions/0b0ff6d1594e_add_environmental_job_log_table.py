"""add_environmental_job_log_table

Revision ID: 0b0ff6d1594e
Revises: 013bbc52a33f
Create Date: 2026-07-18 03:25:33.659385

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0b0ff6d1594e'
down_revision: Union[str, Sequence[str], None] = '013bbc52a33f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'environmental_job_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_name', sa.String(length=100), nullable=False),
        sa.Column('executed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('duration_sec', sa.Float(), nullable=True),
        sa.Column('records_processed', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('records_rejected', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('metadata_json', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_environmental_job_log_job_name',
        'environmental_job_log',
        ['job_name']
    )
    op.create_index(
        'ix_environmental_job_log_executed_at',
        'environmental_job_log',
        ['executed_at']
    )
    op.create_index(
        'ix_environmental_job_log_status',
        'environmental_job_log',
        ['status']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_environmental_job_log_status', 'environmental_job_log')
    op.drop_index('ix_environmental_job_log_executed_at', 'environmental_job_log')
    op.drop_index('ix_environmental_job_log_job_name', 'environmental_job_log')
    op.drop_table('environmental_job_log')
