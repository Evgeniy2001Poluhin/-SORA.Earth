"""merge forecast and environmental branches

Revision ID: 013bbc52a33f
Revises: a8d9becb25de, f7a8b9c0d1e2
Create Date: 2026-07-18 03:25:29.291948

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '013bbc52a33f'
down_revision: Union[str, Sequence[str], None] = ('a8d9becb25de', 'f7a8b9c0d1e2')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
