"""create region_esg_scores and converge legacy shapes onto the ORM schema

No migration ever created this table. It existed only as the RegionESGScore
model in app/database.py, so it appeared through Base.metadata.create_all()
rather than the migration chain. `alembic upgrade head` therefore failed on an
empty database at a1b2c3d4e5f6, which builds a view on top of it, and would
fail again at cb5a035aed2f, whose ADD COLUMN IF NOT EXISTS guards the column
but not the table.

`CREATE TABLE IF NOT EXISTS` alone is not enough: it reports success on a
database whose table exists but disagrees with the model, so drift stays
hidden. The DO block below distinguishes four cases explicitly:

  A. no table            -> create the exact ORM schema
  B. already matching    -> no-op
  C. recognised legacy   -> convert in place, preserving rows
  D. anything else       -> RAISE, naming what disagrees

A DO block rather than an Inspector guard because `alembic upgrade --sql` runs
against a MockConnection, where sa.inspect() raises NoInspectionAvailable. The
same statement is emitted online and offline, so both paths carry identical
semantics.

Target schema, matching RegionESGScore:

    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY
    region_code  VARCHAR(10) NOT NULL, unique via ix_region_esg_scores_region_code
    env_score, social_score, gov_score, total_score, confidence
                 DOUBLE PRECISION NULL
    sources_count, signals_used
                 INTEGER NULL
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()

Revision ID: b7c1e4a92f30
Revises: 31e5cc432377
Create Date: 2026-07-26 20:50:00.000000

"""
from alembic import op


revision = 'b7c1e4a92f30'
down_revision = '31e5cc432377'
branch_labels = None
depends_on = None


CONVERGE_SQL = r"""
DO $migration$
DECLARE
    v_regclass   oid := to_regclass('public.region_esg_scores');
    v_pk_cols    text[];
    v_pk_name    text;
    v_missing    text[];
    v_bad_types  text[];
    v_id_type    text;
    v_dup_ids    bigint;
    v_null_ids   bigint;
    v_fk_count   int;
BEGIN
    ----------------------------------------------------------------- scenario A
    IF v_regclass IS NULL THEN
        CREATE TABLE region_esg_scores (
            id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            region_code   VARCHAR(10) NOT NULL,
            env_score     DOUBLE PRECISION,
            social_score  DOUBLE PRECISION,
            gov_score     DOUBLE PRECISION,
            total_score   DOUBLE PRECISION,
            confidence    DOUBLE PRECISION,
            sources_count INTEGER,
            signals_used  INTEGER,
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX ix_region_esg_scores_region_code
            ON region_esg_scores (region_code);
        RAISE NOTICE 'region_esg_scores created (scenario A: empty database)';
        RETURN;
    END IF;

    -------------------------------------------------- required columns and types
    SELECT array_agg(expected.name ORDER BY expected.name)
      INTO v_missing
      FROM (VALUES
              ('region_code'), ('env_score'), ('social_score'), ('gov_score'),
              ('total_score'), ('confidence'), ('sources_count'),
              ('signals_used'), ('updated_at')
           ) AS expected(name)
     WHERE NOT EXISTS (
              SELECT 1 FROM information_schema.columns c
               WHERE c.table_name = 'region_esg_scores'
                 AND c.column_name = expected.name);

    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION
            'region_esg_scores exists but is missing column(s): %. Refusing to '
            'continue: the existing schema disagrees with RegionESGScore and '
            'converting it automatically could lose data.', v_missing;
    END IF;

    SELECT array_agg(format('%s is %s', c.column_name, c.data_type) ORDER BY c.column_name)
      INTO v_bad_types
      FROM information_schema.columns c
      JOIN (VALUES
              ('region_code',   'character varying'),
              ('env_score',     'double precision'),
              ('social_score',  'double precision'),
              ('gov_score',     'double precision'),
              ('total_score',   'double precision'),
              ('confidence',    'double precision'),
              ('sources_count', 'integer'),
              ('signals_used',  'integer'),
              ('updated_at',    'timestamp with time zone')
           ) AS want(name, typ) ON want.name = c.column_name
     WHERE c.table_name = 'region_esg_scores'
       AND c.data_type <> want.typ;

    IF v_bad_types IS NOT NULL THEN
        RAISE EXCEPTION
            'region_esg_scores exists with incompatible column type(s): %. '
            'Refusing to continue.', v_bad_types;
    END IF;

    ------------------------------------------------------------- surrogate id
    SELECT c.data_type INTO v_id_type
      FROM information_schema.columns c
     WHERE c.table_name = 'region_esg_scores' AND c.column_name = 'id';

    IF v_id_type IS NULL THEN
        ALTER TABLE region_esg_scores
            ADD COLUMN id BIGINT GENERATED ALWAYS AS IDENTITY;
        RAISE NOTICE 'added surrogate id column';
    ELSIF v_id_type NOT IN ('bigint', 'integer') THEN
        RAISE EXCEPTION
            'region_esg_scores.id has type %, expected bigint or integer. '
            'Refusing to continue.', v_id_type;
    END IF;

    SELECT count(*) INTO v_null_ids FROM region_esg_scores WHERE id IS NULL;
    IF v_null_ids > 0 THEN
        RAISE EXCEPTION
            'region_esg_scores has % row(s) with a NULL id; id cannot become the '
            'primary key until they are resolved.', v_null_ids;
    END IF;

    SELECT count(*) INTO v_dup_ids
      FROM (SELECT id FROM region_esg_scores GROUP BY id HAVING count(*) > 1) d;
    IF v_dup_ids > 0 THEN
        RAISE EXCEPTION
            'region_esg_scores has % duplicated id value(s); id cannot become the '
            'primary key until they are resolved.', v_dup_ids;
    END IF;

    ------------------------------------------------------------- primary key
    SELECT array_agg(a.attname ORDER BY a.attname), max(con.conname)
      INTO v_pk_cols, v_pk_name
      FROM pg_constraint con
      JOIN pg_attribute a
        ON a.attrelid = con.conrelid AND a.attnum = ANY (con.conkey)
     WHERE con.conrelid = v_regclass AND con.contype = 'p';

    IF v_pk_cols IS DISTINCT FROM ARRAY['id'] THEN
        -- Anything referencing the current primary key would break, so a manual
        -- migration is required rather than a silent constraint swap.
        SELECT count(*) INTO v_fk_count
          FROM pg_constraint
         WHERE confrelid = v_regclass AND contype = 'f';
        IF v_fk_count > 0 THEN
            RAISE EXCEPTION
                'region_esg_scores is referenced by % foreign key(s); the primary '
                'key cannot be moved to id automatically.', v_fk_count;
        END IF;

        IF v_pk_name IS NOT NULL THEN
            EXECUTE format('ALTER TABLE region_esg_scores DROP CONSTRAINT %I', v_pk_name);
            RAISE NOTICE 'dropped legacy primary key % on %', v_pk_name, v_pk_cols;
        END IF;

        ALTER TABLE region_esg_scores ADD PRIMARY KEY (id);
        RAISE NOTICE 'primary key moved to id (scenario C: legacy schema)';
    END IF;

    ------------------------------------------------- region_code NOT NULL/unique
    ALTER TABLE region_esg_scores ALTER COLUMN region_code SET NOT NULL;

    IF NOT EXISTS (
            SELECT 1 FROM pg_indexes
             WHERE tablename = 'region_esg_scores'
               AND indexname = 'ix_region_esg_scores_region_code') THEN
        CREATE UNIQUE INDEX ix_region_esg_scores_region_code
            ON region_esg_scores (region_code);
        RAISE NOTICE 'created unique index on region_code';
    END IF;
END
$migration$;
"""


def upgrade() -> None:
    op.execute(CONVERGE_SQL)


def downgrade() -> None:
    # regional_esg_snapshot selects from this table, so it has to go first.
    op.execute("DROP VIEW IF EXISTS regional_esg_snapshot")
    op.execute("DROP INDEX IF EXISTS ix_region_esg_scores_region_code")
    op.execute("DROP TABLE IF EXISTS region_esg_scores")
