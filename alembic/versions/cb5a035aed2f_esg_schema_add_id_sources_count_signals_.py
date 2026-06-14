"""esg schema add id sources_count signals_used

Revision ID: cb5a035aed2f
Revises: 0d88c3e3c633
Create Date: 2026-06-14 18:56:32.402876

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cb5a035aed2f'
down_revision: Union[str, Sequence[str], None] = '0d88c3e3c633'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.execute("ALTER TABLE region_esg_scores ADD COLUMN IF NOT EXISTS id BIGINT GENERATED ALWAYS AS IDENTITY")
    op.execute("ALTER TABLE region_esg_scores ADD COLUMN IF NOT EXISTS sources_count INTEGER")
    op.execute("ALTER TABLE region_esg_scores ADD COLUMN IF NOT EXISTS signals_used INTEGER")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_region_esg_scores_id ON region_esg_scores(id)")
def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_region_esg_scores_id")
    op.execute("ALTER TABLE region_esg_scores DROP COLUMN IF EXISTS signals_used")
    op.execute("ALTER TABLE region_esg_scores DROP COLUMN IF EXISTS sources_count")
    op.execute("ALTER TABLE region_esg_scores DROP COLUMN IF EXISTS id")
