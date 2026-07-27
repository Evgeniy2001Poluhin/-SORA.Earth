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
    v_regclass   oid := to_regclass('region_esg_scores');
    v_pk_cols    text[];
    v_pk_name    text;
    v_missing    text[];
    v_bad_types  text[];
    v_id_type    text;
    v_dup_ids    bigint;
    v_null_ids   bigint;
    v_fk_count   int;
    v_unrecognised text[];
    v_max_code_len int;
    v_view_oid   oid;
    v_view_def   text;
    v_view_owner text;
    v_view_grants text[];
    v_grant      text;
    v_col        text;
    v_max_id     bigint;
    v_seq        text;
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
              SELECT 1 FROM pg_attribute a
               WHERE a.attrelid = v_regclass
                 AND a.attnum > 0 AND NOT a.attisdropped
                 AND a.attname = expected.name);

    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION
            'region_esg_scores exists but is missing column(s): %. Refusing to '
            'continue: the existing schema disagrees with RegionESGScore and '
            'converting it automatically could lose data.', v_missing;
    END IF;

    SELECT array_agg(format('%s is %s', a.attname, format_type(a.atttypid, NULL)) ORDER BY a.attname)
      INTO v_bad_types
      FROM pg_attribute a
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
           ) AS want(name, typ) ON want.name = a.attname
     WHERE a.attrelid = v_regclass
       AND a.attnum > 0 AND NOT a.attisdropped
       AND format_type(a.atttypid, NULL) <> want.typ;

    ------------------------------------------- canonicalise recognised legacy types
    -- Production carries region_code as text and the score columns as real,
    -- neither of which matches RegionESGScore. Accepting them as equivalent
    -- would leave a clean install, the ORM metadata and production permanently
    -- disagreeing, so they are converted rather than tolerated. Only these two
    -- shapes are recognised; anything else still reaches the RAISE below.
    IF v_bad_types IS NOT NULL THEN
        -- Serialise against another migration attempting the same conversion.
        PERFORM pg_advisory_xact_lock(hashtext('region_esg_scores_converge'));

        SELECT array_agg(format('%s is %s', a.attname, format_type(a.atttypid, NULL)) ORDER BY a.attname)
          INTO v_unrecognised
          FROM pg_attribute a
          JOIN (VALUES
                  ('region_code',   'character varying', 'text'),
                  ('env_score',     'double precision',  'real'),
                  ('social_score',  'double precision',  'real'),
                  ('gov_score',     'double precision',  'real'),
                  ('total_score',   'double precision',  'real'),
                  ('confidence',    'double precision',  'real'),
                  ('sources_count', 'integer',           'integer'),
                  ('signals_used',  'integer',           'integer'),
                  ('updated_at',    'timestamp with time zone', 'timestamp with time zone')
               ) AS want(name, canonical, legacy) ON want.name = a.attname
         WHERE a.attrelid = v_regclass
           AND a.attnum > 0 AND NOT a.attisdropped
           AND format_type(a.atttypid, NULL) NOT IN (want.canonical, want.legacy);

        IF v_unrecognised IS NOT NULL THEN
            RAISE EXCEPTION
                'region_esg_scores has column type(s) that are neither canonical '
                'nor a recognised legacy shape: %. Refusing to continue.', v_unrecognised;
        END IF;

        -- Guard the narrowing conversion before touching anything.
        IF EXISTS (SELECT 1 FROM pg_attribute a
                    WHERE a.attrelid = v_regclass AND a.attname = 'region_code'
                      AND format_type(a.atttypid, NULL) = 'text') THEN
            SELECT max(length(region_code)) INTO v_max_code_len FROM region_esg_scores;
            IF coalesce(v_max_code_len, 0) > 10 THEN
                RAISE EXCEPTION
                    'region_code holds value(s) up to % characters; converting to '
                    'VARCHAR(10) would truncate data. Refusing to continue.',
                    v_max_code_len;
            END IF;
        END IF;

        -- A view over these columns blocks ALTER COLUMN TYPE, so capture it
        -- exactly -- definition, owner and grants -- and restore it afterwards.
        SELECT c.oid, pg_get_viewdef(c.oid, true), pg_get_userbyid(c.relowner)
          INTO v_view_oid, v_view_def, v_view_owner
          FROM pg_class c
         WHERE c.relname = 'regional_esg_snapshot'
           AND c.relkind = 'v'
           AND c.relnamespace = (SELECT relnamespace FROM pg_class WHERE oid = v_regclass);

        IF v_view_oid IS NOT NULL THEN
            SELECT array_agg(format('GRANT %s ON regional_esg_snapshot TO %I',
                                    acl.privilege_type, acl.grantee::regrole))
              INTO v_view_grants
              FROM (SELECT (aclexplode(c.relacl)).* FROM pg_class c WHERE c.oid = v_view_oid) acl;

            EXECUTE 'DROP VIEW regional_esg_snapshot';
            RAISE NOTICE 'dropped regional_esg_snapshot for the duration of the conversion';
        END IF;

        -- real -> double precision is widening and lossless.
        FOR v_col IN
            SELECT a.attname FROM pg_attribute a
             WHERE a.attrelid = v_regclass AND a.attnum > 0 AND NOT a.attisdropped
               AND format_type(a.atttypid, NULL) = 'real'
        LOOP
            EXECUTE format(
                'ALTER TABLE region_esg_scores ALTER COLUMN %I TYPE DOUBLE PRECISION '
                'USING %I::double precision', v_col, v_col);
            RAISE NOTICE 'converted % from real to double precision', v_col;
        END LOOP;

        IF EXISTS (SELECT 1 FROM pg_attribute a
                    WHERE a.attrelid = v_regclass AND a.attname = 'region_code'
                      AND format_type(a.atttypid, NULL) = 'text') THEN
            ALTER TABLE region_esg_scores
                ALTER COLUMN region_code TYPE VARCHAR(10) USING region_code::varchar(10);
            RAISE NOTICE 'converted region_code from text to VARCHAR(10)';
        END IF;

        IF v_view_oid IS NOT NULL THEN
            EXECUTE 'CREATE VIEW regional_esg_snapshot AS ' || v_view_def;
            EXECUTE format('ALTER VIEW regional_esg_snapshot OWNER TO %I', v_view_owner);
            IF v_view_grants IS NOT NULL THEN
                FOREACH v_grant IN ARRAY v_view_grants LOOP
                    EXECUTE v_grant;
                END LOOP;
            END IF;
            RAISE NOTICE 'restored regional_esg_snapshot with its owner and grants';
        END IF;
    END IF;

    ------------------------------------------------------------- surrogate id
    SELECT format_type(a.atttypid, NULL) INTO v_id_type
      FROM pg_attribute a
     WHERE a.attrelid = v_regclass
       AND a.attnum > 0 AND NOT a.attisdropped
       AND a.attname = 'id';

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
            SELECT 1 FROM pg_index i
              JOIN pg_class ic ON ic.oid = i.indexrelid
             WHERE i.indrelid = v_regclass
               AND ic.relname = 'ix_region_esg_scores_region_code') THEN
        CREATE UNIQUE INDEX ix_region_esg_scores_region_code
            ON region_esg_scores (region_code);
        RAISE NOTICE 'created unique index on region_code';
    END IF;

    ------------------------------------------------------------ identity state
    -- Rows that predate the identity column keep their ids, so the sequence has
    -- to be moved past them or the next insert collides.
    SELECT pg_get_serial_sequence('region_esg_scores', 'id') INTO v_seq;
    IF v_seq IS NOT NULL THEN
        SELECT max(id) INTO v_max_id FROM region_esg_scores;
        IF v_max_id IS NOT NULL THEN
            PERFORM setval(v_seq, v_max_id, true);
            RAISE NOTICE 'identity sequence aligned to max(id) = %', v_max_id;
        END IF;
    END IF;

    ------------------------------------------------------------- postconditions
    -- Fail loudly rather than reporting success on a half-converted table.
    IF (SELECT array_agg(a.attname ORDER BY a.attname)
          FROM pg_constraint con
          JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = ANY (con.conkey)
         WHERE con.conrelid = v_regclass AND con.contype = 'p')
       IS DISTINCT FROM ARRAY['id']::name[] THEN
        RAISE EXCEPTION 'postcondition failed: primary key is not on id';
    END IF;

    IF EXISTS (SELECT 1 FROM pg_attribute a
                JOIN (VALUES ('region_code', 'character varying'),
                             ('env_score', 'double precision'),
                             ('social_score', 'double precision'),
                             ('gov_score', 'double precision'),
                             ('total_score', 'double precision'),
                             ('confidence', 'double precision')
                     ) AS want(name, typ) ON want.name = a.attname
               WHERE a.attrelid = v_regclass
                 AND a.attnum > 0 AND NOT a.attisdropped
                 AND format_type(a.atttypid, NULL) <> want.typ) THEN
        RAISE EXCEPTION 'postcondition failed: column types are not canonical';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_index i
                     JOIN pg_class ic ON ic.oid = i.indexrelid
                    WHERE i.indrelid = v_regclass
                      AND ic.relname = 'ix_region_esg_scores_region_code'
                      AND i.indisunique) THEN
        RAISE EXCEPTION 'postcondition failed: region_code is not uniquely indexed';
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
