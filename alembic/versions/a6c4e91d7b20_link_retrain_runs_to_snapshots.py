"""link retrain runs to ordered immutable snapshots

Revision ID: a6c4e91d7b20
Revises: f4b7c2d91e06
"""
from alembic import op


revision = "a6c4e91d7b20"
down_revision = "f4b7c2d91e06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A deployed database can reach this revision by two supported paths:
    # Alembic creates the column here, while the historical create_all() path
    # has already created it from the current ORM model. Accept only the exact
    # shape the migration owns; IF NOT EXISTS alone would silently bless drift.
    # A DO block keeps the same semantics in online and ``alembic --sql`` mode.
    op.execute(r"""
DO $snapshot_linkage$
DECLARE
    v_table regclass;
    v_type text;
    v_not_null boolean;
    v_default text;
    v_identity "char";
    v_generated "char";
BEGIN
    v_table := to_regclass('retrain_log');
    IF v_table IS NULL THEN
        RAISE EXCEPTION
            'snapshot linkage migration refuses to complete: retrain_log is missing';
    END IF;

    SELECT format_type(a.atttypid, a.atttypmod),
           a.attnotnull,
           pg_get_expr(d.adbin, d.adrelid),
           a.attidentity,
           a.attgenerated
      INTO v_type, v_not_null, v_default, v_identity, v_generated
      FROM pg_attribute a
      LEFT JOIN pg_attrdef d
        ON d.adrelid = a.attrelid AND d.adnum = a.attnum
     WHERE a.attrelid = v_table
       AND a.attname = 'snapshot_ids_json'
       AND a.attnum > 0
       AND NOT a.attisdropped;

    IF NOT FOUND THEN
        ALTER TABLE retrain_log ADD COLUMN snapshot_ids_json TEXT;
    ELSIF v_type <> 'text'
       OR v_not_null
       OR v_default IS NOT NULL
       OR v_identity <> ''
       OR v_generated <> '' THEN
        RAISE EXCEPTION USING MESSAGE =
            'snapshot linkage migration refuses to complete: '
            'retrain_log.snapshot_ids_json must be nullable text without '
            'default, identity or generated expression; found type=' || v_type
            || ', not_null=' || v_not_null::text
            || ', default=' || coalesce(v_default, '<none>')
            || ', identity=' || coalesce(v_identity::text, '<none>')
            || ', generated=' || coalesce(v_generated::text, '<none>');
    END IF;
END
$snapshot_linkage$;
""")


def downgrade() -> None:
    op.execute("ALTER TABLE retrain_log DROP COLUMN IF EXISTS snapshot_ids_json")
