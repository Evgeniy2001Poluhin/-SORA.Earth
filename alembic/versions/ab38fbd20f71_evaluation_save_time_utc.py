"""Record a timezone-aware save time for new evaluations."""

import sqlalchemy as sa
from alembic import op

revision = "ab38fbd20f71"
down_revision = "b7d41f2e9c35"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("evaluations", sa.Column("saved_at_utc", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("evaluations", "saved_at_utc")
