"""widen source_revision to hold a content-hash revision

`source_revision` was String(64). A content-hash revision is `rev:v1:` plus a
64-character sha256 -- 71 characters. Both literal ingesters were therefore
unable to write a single row: PostgreSQL raised StringDataRightTruncation and
the whole persist transaction rolled back.

It passed every test because SQLite does not enforce VARCHAR length. Only
production refused, and only when the ingesters were actually run (#121).

Widened rather than truncating the digest. Shortening the hash to fit would
make the stored revision a different thing from the one computed, and hide the
constraint instead of stating it.

128 to match `source_record_id`, which is already that wide and holds the same
shape of value: measured across all 85 regions of both literal sources, the
longest identity is 71 characters (`obs:v2:` plus the digest) and the longest
revision is 71. The headroom is for a longer prefix or a wider digest, not for
anything either side produces today.

Revision ID: b8f1d2c73a05
Revises: c7e94b2a83f1
Create Date: 2026-08-13
"""
from alembic import op
import sqlalchemy as sa


revision = "b8f1d2c73a05"
down_revision = "c7e94b2a83f1"
branch_labels = None
depends_on = None

TABLE = "environmental_observations"


def upgrade() -> None:
    op.alter_column(TABLE, "source_revision",
                    existing_type=sa.String(64), type_=sa.String(128),
                    existing_nullable=True)


def downgrade() -> None:
    """Refuses if anything would be truncated.

    Narrowing a column silently cuts the values that no longer fit, and a
    revision cut in half is not a shorter revision -- it is a different one
    that still looks valid.
    """
    conn = op.get_bind()
    too_long = conn.execute(sa.text(
        f"SELECT count(*) FROM {TABLE} WHERE length(source_revision) > 64")).scalar()
    if too_long:
        raise RuntimeError(
            f"refusing to narrow source_revision: {too_long} row(s) hold a value "
            f"longer than 64 characters and would be truncated into something "
            f"that still parses but is not the revision that was computed"
        )
    op.alter_column(TABLE, "source_revision",
                    existing_type=sa.String(128), type_=sa.String(64),
                    existing_nullable=True)
