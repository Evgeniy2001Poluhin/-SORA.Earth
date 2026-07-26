"""create region_esg_scores, the table the regional_esg_snapshot view reads

No migration ever created this table. It existed only as the RegionESGScore
model in app/database.py, so it appeared through Base.metadata.create_all()
rather than through the migration chain. That made `alembic upgrade head` fail
on an empty database at a1b2c3d4e5f6, which builds a view on top of it, and
again at cb5a035aed2f, whose ADD COLUMN IF NOT EXISTS guards the column but not
the table.

Everything here is emitted as idempotent DDL rather than op.create_table behind
an inspector check, for two reasons:

  * `alembic upgrade --sql` runs against a MockConnection, so sa.inspect() is
    unavailable and an inspector-based guard raises NoInspectionAvailable.
    Idempotent SQL keeps offline script generation working.
  * Databases bootstrapped through create_all() -- production among them --
    already carry the table, and IF NOT EXISTS leaves them untouched, including
    the columns cb5a035aed2f adds afterwards.

`id` is declared BIGINT GENERATED ALWAYS AS IDENTITY to match what cb5a035aed2f
adds to pre-existing databases; otherwise a freshly migrated schema and the
production schema would disagree on that column's type.

The chain is PostgreSQL-only already -- a1b2c3d4e5f6 uses CREATE OR REPLACE
VIEW, which SQLite rejects -- so PostgreSQL syntax is consistent here.

Revision ID: b7c1e4a92f30
Revises: 31e5cc432377
Create Date: 2026-07-26 20:50:00.000000

"""
from alembic import op


revision = 'b7c1e4a92f30'
down_revision = '31e5cc432377'
branch_labels = None
depends_on = None


# region_code is the natural key the ESG aggregator upserts on and the model
# declares it unique, so it carries the primary key. cb5a035aed2f later adds a
# surrogate id with its own unique index on databases that predate this file;
# declaring id here keeps both paths converged on the same column type.
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS region_esg_scores (
    id            BIGINT GENERATED ALWAYS AS IDENTITY,
    region_code   VARCHAR(10) NOT NULL,
    env_score     DOUBLE PRECISION,
    social_score  DOUBLE PRECISION,
    gov_score     DOUBLE PRECISION,
    total_score   DOUBLE PRECISION,
    confidence    DOUBLE PRECISION,
    sources_count INTEGER,
    signals_used  INTEGER,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_region_esg_scores PRIMARY KEY (region_code)
);
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS ix_region_esg_scores_region_code
    ON region_esg_scores (region_code);
"""


def upgrade() -> None:
    op.execute(CREATE_TABLE_SQL)
    op.execute(CREATE_INDEX_SQL)


def downgrade() -> None:
    # regional_esg_snapshot selects from this table, so it has to go first.
    op.execute("DROP VIEW IF EXISTS regional_esg_snapshot")
    op.execute("DROP INDEX IF EXISTS ix_region_esg_scores_region_code")
    op.execute("DROP TABLE IF EXISTS region_esg_scores")
