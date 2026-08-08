"""mark a region score that is no longer backed by observations

A score whose region has gone incomplete or stale is deliberately not
overwritten -- a partial recomputation would be worse than a preserved one.
Without somewhere to record that, the row stays readable as a current score and
nothing tells the reader otherwise, which is #116 moved to the consumer
boundary.

`stale_since` records when the row *first* stopped being backed, so it survives
subsequent runs; `stale_reason` records why. Both are cleared when the region
becomes complete again.

Additive and nullable: existing rows read as not-stale, which is the correct
default -- they were written from a complete set under the previous contract.

Revision ID: d3f0a71c9b48
Revises: c58e21a9f7d4
Create Date: 2026-08-08

"""
from alembic import op
import sqlalchemy as sa


revision = "d3f0a71c9b48"
down_revision = "c58e21a9f7d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("region_esg_scores")}

    if "stale_since" not in existing:
        op.add_column(
            "region_esg_scores",
            sa.Column("stale_since", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index(
            "ix_region_esg_scores_stale_since",
            "region_esg_scores",
            ["stale_since"],
        )
    if "stale_reason" not in existing:
        op.add_column(
            "region_esg_scores",
            sa.Column("stale_reason", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("region_esg_scores")}

    if "stale_reason" in existing:
        op.drop_column("region_esg_scores", "stale_reason")
    if "stale_since" in existing:
        op.drop_index("ix_region_esg_scores_stale_since", "region_esg_scores")
        op.drop_column("region_esg_scores", "stale_since")
