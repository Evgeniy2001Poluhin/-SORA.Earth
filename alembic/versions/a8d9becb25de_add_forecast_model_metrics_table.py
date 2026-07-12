"""add_forecast_model_metrics_table

Revision ID: a8d9becb25de
Revises: cb5a035aed2f
Create Date: 2026-07-12 23:54:23.776395

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a8d9becb25de'
down_revision: Union[str, Sequence[str], None] = 'cb5a035aed2f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'forecast_model_metrics',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('trained_at', sa.DateTime(), nullable=False),
        sa.Column('metric_name', sa.String(length=50), nullable=False),
        sa.Column('model_type', sa.String(length=50), nullable=False),
        sa.Column('train_samples', sa.Integer(), nullable=False),
        sa.Column('test_samples', sa.Integer(), nullable=True),
        sa.Column('mae', sa.Float(), nullable=True),
        sa.Column('rmse', sa.Float(), nullable=True),
        sa.Column('mape', sa.Float(), nullable=True),
        sa.Column('r2_score', sa.Float(), nullable=True),
        sa.Column('training_duration_sec', sa.Float(), nullable=True),
        sa.Column('trigger_source', sa.String(length=50), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('metadata_json', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_forecast_model_metrics_id', 'forecast_model_metrics', ['id'])
    op.create_index('ix_forecast_model_metrics_trained_at', 'forecast_model_metrics', ['trained_at'])
    op.create_index('ix_forecast_model_metrics_metric_name', 'forecast_model_metrics', ['metric_name'])
    op.create_index('ix_forecast_model_metrics_model_type', 'forecast_model_metrics', ['model_type'])
    op.create_index('ix_forecast_model_metrics_trigger_source', 'forecast_model_metrics', ['trigger_source'])
    op.create_index('ix_forecast_model_metrics_status', 'forecast_model_metrics', ['status'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_forecast_model_metrics_status', table_name='forecast_model_metrics')
    op.drop_index('ix_forecast_model_metrics_trigger_source', table_name='forecast_model_metrics')
    op.drop_index('ix_forecast_model_metrics_model_type', table_name='forecast_model_metrics')
    op.drop_index('ix_forecast_model_metrics_metric_name', table_name='forecast_model_metrics')
    op.drop_index('ix_forecast_model_metrics_trained_at', table_name='forecast_model_metrics')
    op.drop_index('ix_forecast_model_metrics_id', table_name='forecast_model_metrics')
    op.drop_table('forecast_model_metrics')
