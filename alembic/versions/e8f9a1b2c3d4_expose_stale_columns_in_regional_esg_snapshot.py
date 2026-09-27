"""expose stale columns in regional_esg_snapshot view

The backend writes stale_since and stale_reason to region_esg_scores when a
region's inputs stop being fresh (app/services/esg_aggregator.py _mark_stale),
but the view regional_esg_snapshot does not expose them, so app/routes/map_russia.py
cannot show that a score is no longer backed by current data. This makes stale
scores indistinguishable from fresh ones at the consumer boundary, which is #116
moved to the map layer.

The view is recreated with two additional columns appended at the end:
stale_since and stale_reason. PostgreSQL allows appending columns to a view via
CREATE OR REPLACE VIEW; columns cannot be dropped or reordered this way, so the
existing 8 columns are preserved in their current order and expression.

Revision ID: e8f9a1b2c3d4
Revises: d2a7f4b81c65
Create Date: 2026-09-27

"""
from alembic import op

revision = 'e8f9a1b2c3d4'
down_revision = 'd2a7f4b81c65'
branch_labels = None
depends_on = None

# The view is recreated with 10 columns: the original 8 unchanged, plus
# stale_since and stale_reason appended. CREATE OR REPLACE VIEW allows appending
# only; the downgrade cannot remove them the same way, so it drops and recreates
# the view with the previous definition, preserving owner and grants the way
# b7c1e4a92f30 does.
UPGRADE_SQL = """
CREATE OR REPLACE VIEW regional_esg_snapshot AS
SELECT region_code,
       env_score    AS e_score,
       social_score AS s_score,
       gov_score    AS g_score,
       total_score  AS score,
       confidence,
       ARRAY[]::text[] AS sources_used,
       updated_at   AS computed_at,
       stale_since,
       stale_reason
FROM region_esg_scores;
"""

DOWNGRADE_SQL = """
DO $downgrade$
DECLARE
    v_view_owner text;
    v_view_grants text[];
    v_grant text;
BEGIN
    -- Capture owner and grants before dropping the view.
    SELECT pg_get_userbyid(c.relowner)
      INTO v_view_owner
      FROM pg_class c
     WHERE c.relname = 'regional_esg_snapshot'
       AND c.relkind = 'v'
       AND c.relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public');

    IF v_view_owner IS NULL THEN
        RAISE NOTICE 'regional_esg_snapshot is absent; nothing to downgrade';
        RETURN;
    END IF;

    -- grantee 0 is PUBLIC, which renders as "-" through regrole and needs the
    -- PUBLIC keyword, not a quoted identifier.
    SELECT array_agg(format('GRANT %s ON regional_esg_snapshot TO %s',
                            acl.privilege_type,
                            CASE WHEN acl.grantee = 0 THEN 'PUBLIC'
                                 ELSE quote_ident(pg_get_userbyid(acl.grantee)) END))
      INTO v_view_grants
      FROM (SELECT (aclexplode(c.relacl)).*
              FROM pg_class c
             WHERE c.relname = 'regional_esg_snapshot' AND c.relkind = 'v') acl;

    DROP VIEW regional_esg_snapshot;

    -- Restore the previous definition (8 columns, no stale_since/stale_reason).
    CREATE VIEW regional_esg_snapshot AS
    SELECT region_code,
           env_score    AS e_score,
           social_score AS s_score,
           gov_score    AS g_score,
           total_score  AS score,
           confidence,
           ARRAY[]::text[] AS sources_used,
           updated_at   AS computed_at
    FROM region_esg_scores;

    EXECUTE format('ALTER VIEW regional_esg_snapshot OWNER TO %I', v_view_owner);

    IF v_view_grants IS NOT NULL THEN
        FOREACH v_grant IN ARRAY v_view_grants LOOP
            EXECUTE v_grant;
        END LOOP;
    END IF;

    RAISE NOTICE 'downgraded regional_esg_snapshot to 8 columns, preserving owner and grants';
END
$downgrade$;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
