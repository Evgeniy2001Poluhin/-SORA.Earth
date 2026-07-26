"""create region_esg_scores, the table the regional_esg_snapshot view reads

No migration ever created this table. It existed only as the RegionESGScore
model in app/database.py, so it appeared through Base.metadata.create_all()
rather than through the migration chain. That made `alembic upgrade head` fail
on an empty database at a1b2c3d4e5f6, which builds a view on top of it, and
again at cb5a035aed2f, which alters it.

Creation is guarded by an inspector check so databases that already carry the
table -- including production, bootstrapped via create_all() -- are untouched
and the columns cb5a035aed2f adds are preserved.

Revision ID: b7c1e4a92f30
Revises: 31e5cc432377
Create Date: 2026-07-26 20:50:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b7c1e4a92f30'
down_revision = '31e5cc432377'
branch_labels = None
depends_on = None

TABLE_NAME = "region_esg_scores"


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    if _has_table(TABLE_NAME):
        # Already present from an earlier create_all() bootstrap.
        return

    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("region_code", sa.String(length=10), nullable=False),
        sa.Column("env_score", sa.Float(), nullable=True),
        sa.Column("social_score", sa.Float(), nullable=True),
        sa.Column("gov_score", sa.Float(), nullable=True),
        sa.Column("total_score", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("sources_count", sa.Integer(), nullable=True),
        sa.Column("signals_used", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("region_code", name="uq_region_esg_scores_region_code"),
    )
    op.create_index(
        "ix_region_esg_scores_region_code", TABLE_NAME, ["region_code"], unique=False
    )


def downgrade() -> None:
    # regional_esg_snapshot selects from this table, so it has to go first.
    op.execute("DROP VIEW IF EXISTS regional_esg_snapshot")
    op.drop_index("ix_region_esg_scores_region_code", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
