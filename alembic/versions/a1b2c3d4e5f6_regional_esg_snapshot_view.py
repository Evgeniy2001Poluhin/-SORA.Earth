"""regional_esg_snapshot view (maps region_esg_scores -> map_russia API)

Revision ID: a1b2c3d4e5f6
Revises: 31e5cc432377
Create Date: 2026-06-07 22:10:00.000000

"""
from alembic import op

revision = 'a1b2c3d4e5f6'
down_revision = 'b7c1e4a92f30'
branch_labels = None
depends_on = None

VIEW_SQL = """
CREATE OR REPLACE VIEW regional_esg_snapshot AS
SELECT region_code,
       env_score    AS e_score,
       social_score AS s_score,
       gov_score    AS g_score,
       total_score  AS score,
       confidence,
       ARRAY[]::text[] AS sources_used,
       updated_at   AS computed_at
FROM region_esg_scores;
"""

def upgrade() -> None:
    op.execute(VIEW_SQL)

def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS regional_esg_snapshot;")
