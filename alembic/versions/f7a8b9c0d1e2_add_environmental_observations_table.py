"""add environmental_observations table

Revision ID: f7a8b9c0d1e2
Revises: cb5a035aed2f
Create Date: 2026-07-18 00:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'f7a8b9c0d1e2'
down_revision = 'cb5a035aed2f'
branch_labels = None
depends_on = None


def upgrade():
    # Create environmental_observations table
    op.create_table(
        'environmental_observations',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),

        # Geographic location
        sa.Column('region_id', sa.String(length=32), nullable=False),
        sa.Column('country_code', sa.String(length=3), nullable=True),
        sa.Column('latitude', sa.Float(), nullable=True),
        sa.Column('longitude', sa.Float(), nullable=True),

        # Measurement
        sa.Column('indicator', sa.String(length=64), nullable=False),
        sa.Column('value', sa.Float(), nullable=True),
        sa.Column('unit', sa.String(length=32), nullable=True),

        # Provenance
        sa.Column('source', sa.String(length=64), nullable=False),
        sa.Column('source_record_id', sa.String(length=128), nullable=True),
        sa.Column('source_revision', sa.String(length=64), nullable=True),

        # Temporal tracking (UTC-aware)
        sa.Column('event_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ingested_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),

        # Data quality
        sa.Column('quality_score', sa.Float(), nullable=True),
        sa.Column('is_valid', sa.Boolean(), nullable=False, server_default=sa.text('true')),

        # Metadata
        sa.Column('metadata_json', sa.Text(), nullable=True),

        # Audit trail
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),

        sa.PrimaryKeyConstraint('id')
    )

    # Indexes for efficient time-series queries
    op.create_index(
        'ix_environmental_observations_region_id',
        'environmental_observations',
        ['region_id'],
        unique=False
    )
    op.create_index(
        'ix_environmental_observations_country_code',
        'environmental_observations',
        ['country_code'],
        unique=False
    )
    op.create_index(
        'ix_environmental_observations_indicator',
        'environmental_observations',
        ['indicator'],
        unique=False
    )
    op.create_index(
        'ix_environmental_observations_source',
        'environmental_observations',
        ['source'],
        unique=False
    )
    op.create_index(
        'ix_environmental_observations_event_time',
        'environmental_observations',
        ['event_time'],
        unique=False
    )
    op.create_index(
        'ix_environmental_observations_ingested_at',
        'environmental_observations',
        ['ingested_at'],
        unique=False
    )
    op.create_index(
        'ix_environmental_observations_is_valid',
        'environmental_observations',
        ['is_valid'],
        unique=False
    )

    # Composite indexes for common query patterns
    op.create_index(
        'ix_environmental_observations_region_indicator_time',
        'environmental_observations',
        ['region_id', 'indicator', 'event_time'],
        unique=False
    )
    op.create_index(
        'ix_environmental_observations_source_ingested',
        'environmental_observations',
        ['source', 'ingested_at'],
        unique=False
    )

    # Unique constraint to prevent duplicate observations from same source
    # Only enforce when source_record_id is not NULL
    op.create_index(
        'ix_environmental_observations_source_record_unique',
        'environmental_observations',
        ['source', 'source_record_id'],
        unique=True,
        postgresql_where=sa.text('source_record_id IS NOT NULL')
    )


def downgrade():
    op.drop_index('ix_environmental_observations_source_record_unique', table_name='environmental_observations')
    op.drop_index('ix_environmental_observations_source_ingested', table_name='environmental_observations')
    op.drop_index('ix_environmental_observations_region_indicator_time', table_name='environmental_observations')
    op.drop_index('ix_environmental_observations_is_valid', table_name='environmental_observations')
    op.drop_index('ix_environmental_observations_ingested_at', table_name='environmental_observations')
    op.drop_index('ix_environmental_observations_event_time', table_name='environmental_observations')
    op.drop_index('ix_environmental_observations_source', table_name='environmental_observations')
    op.drop_index('ix_environmental_observations_indicator', table_name='environmental_observations')
    op.drop_index('ix_environmental_observations_country_code', table_name='environmental_observations')
    op.drop_index('ix_environmental_observations_region_id', table_name='environmental_observations')
    op.drop_table('environmental_observations')
