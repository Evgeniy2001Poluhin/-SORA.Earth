"""create region_esg_scores table

Revision ID: 2e8b4493b24b
Revises: 31e5cc432377
Create Date: 2026-07-24 23:45:00.000000

Bootstrap fix for GAP-001: region_esg_scores table was referenced by VIEW
creation (a1b2c3d4e5f6) and ALTER TABLE operations (cb5a035aed2f) but was
never created by any prior migration.

This migration creates the minimal base schema required before a1b2c3d4e5f6.
Columns id, sources_count, and signals_used are NOT included here — they are
added later by migration cb5a035aed2f to avoid duplicate column errors.

Primary key on region_code matches production usage pattern and application
code usage (filter_by region_code for upsert operations in esg_aggregator.py).

ORM drift (SQLAlchemy model expects id as primary key) is tracked separately
in GAP-017 and requires separate impact analysis before modification.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '2e8b4493b24b'
down_revision: Union[str, Sequence[str], None] = '31e5cc432377'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'region_esg_scores',
        sa.Column('region_code', sa.Text(), nullable=False),
        sa.Column('env_score', postgresql.REAL(), nullable=True),
        sa.Column('social_score', postgresql.REAL(), nullable=True),
        sa.Column('gov_score', postgresql.REAL(), nullable=True),
        sa.Column('total_score', postgresql.REAL(), nullable=True),
        sa.Column('confidence', postgresql.REAL(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('region_code', name='region_esg_scores_pkey')
    )


def downgrade() -> None:
    # VIEW regional_esg_snapshot is dropped before this (via a1b2c3d4e5f6 downgrade)
    # in correct Alembic chain order, so no CASCADE needed
    op.drop_table('region_esg_scores')
