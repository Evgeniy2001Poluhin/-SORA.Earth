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
    v_id_identity char;
    v_id_default text;
    v_dup_ids    bigint;
    v_null_ids   bigint;
    v_fk_count   int;
    v_unrecognised text[];
    v_max_code_len int;
    v_view_oid   oid;
    v_view_def   text;
    v_view_owner text;
    v_view_grants text[];
    v_view_opts  text[];
    v_view_comment text;
    v_unexpected_deps text[];
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

        -- Every dependent object has to be known before anything is dropped:
        -- an unexpected one would be destroyed or would block the ALTER halfway.
        SELECT array_agg(DISTINCT dep.relname || ' (' || dep.relkind::text || ')')
          INTO v_unexpected_deps
          FROM pg_depend d
          JOIN pg_rewrite rw ON rw.oid = d.objid
          JOIN pg_class dep  ON dep.oid = rw.ev_class
         WHERE d.refobjid = v_regclass
           AND dep.oid <> v_regclass
           AND dep.relname <> 'regional_esg_snapshot';

        IF v_unexpected_deps IS NOT NULL THEN
            RAISE EXCEPTION
                'region_esg_scores has dependent object(s) this migration does not '
                'know how to preserve: %. Refusing to continue.', v_unexpected_deps;
        END IF;

        -- A view over these columns blocks ALTER COLUMN TYPE, so capture it
        -- exactly and restore it afterwards.
        SELECT c.oid, pg_get_viewdef(c.oid, true), pg_get_userbyid(c.relowner),
               c.reloptions, obj_description(c.oid, 'pg_class')
          INTO v_view_oid, v_view_def, v_view_owner, v_view_opts, v_view_comment
          FROM pg_class c
         WHERE c.relname = 'regional_esg_snapshot'
           AND c.relkind = 'v'
           AND c.relnamespace = (SELECT relnamespace FROM pg_class WHERE oid = v_regclass);

        IF v_view_oid IS NOT NULL THEN
            -- CREATE VIEW ... AS <def> carries none of these, so refuse rather
            -- than silently drop them.
            IF v_view_opts IS NOT NULL THEN
                RAISE EXCEPTION
                    'regional_esg_snapshot carries view options % (security_barrier, '
                    'security_invoker or check_option) that recreating it would lose. '
                    'Refusing to continue.', v_view_opts;
            END IF;

            IF EXISTS (SELECT 1 FROM pg_attribute a
                        WHERE a.attrelid = v_view_oid AND a.attnum > 0
                          AND col_description(v_view_oid, a.attnum) IS NOT NULL) THEN
                RAISE EXCEPTION
                    'regional_esg_snapshot has column comment(s) that recreating it '
                    'would lose. Refusing to continue.';
            END IF;

            -- Restorability is checked before the DROP: the transaction would roll
            -- back either way, but failing here names the cause instead of surfacing
            -- an obscure permission error midway through the DDL.
            IF NOT pg_has_role(current_user, v_view_owner, 'MEMBER') THEN
                RAISE EXCEPTION
                    'current user cannot restore regional_esg_snapshot to its owner %. '
                    'Refusing to continue.', v_view_owner;
            END IF;

            -- grantee 0 is PUBLIC, which has no row in pg_roles and renders as "-"
            -- through regrole; it needs the PUBLIC keyword, not a quoted identifier.
            SELECT array_agg(format('GRANT %s ON regional_esg_snapshot TO %s',
                                    acl.privilege_type,
                                    CASE WHEN acl.grantee = 0 THEN 'PUBLIC'
                                         ELSE quote_ident(pg_get_userbyid(acl.grantee)) END))
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
            IF v_view_comment IS NOT NULL THEN
                EXECUTE format('COMMENT ON VIEW regional_esg_snapshot IS %L', v_view_comment);
            END IF;
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

    -- The type alone says nothing about where a value comes from. A plain
    -- bigint passes the check above, takes the primary key below, and then
    -- generates nothing -- so the table looks converged while the first ORM
    -- insert, which omits id, fails on NOT NULL. Catalogue state has to be
    -- classified, not assumed.
    SELECT a.attidentity, pg_get_expr(d.adbin, d.adrelid)
      INTO v_id_identity, v_id_default
      FROM pg_attribute a
      LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
     WHERE a.attrelid = v_regclass
       AND a.attnum > 0 AND NOT a.attisdropped
       AND a.attname = 'id';

    IF v_id_identity IN ('a', 'd') THEN
        NULL;   -- already an identity column
    ELSIF v_id_default IS NOT NULL AND v_id_default LIKE 'nextval(%' THEN
        -- Legacy serial. Sequence-backed, so inserts omitting id already work.
        -- Converting it to an identity column would be churn for uniformity
        -- rather than for behaviour, and every conversion is a chance to lose a
        -- row, so it is left as it is.
        RAISE NOTICE 'id is sequence-backed (legacy serial); left as is';
    ELSIF v_id_default IS NULL THEN
        -- GENERATED ALWAYS rather than BY DEFAULT, to match what a fresh
        -- install and cb5a035aed2f both produce: one canonical shape is worth
        -- more than tolerating an explicit-id insert path that nothing here
        -- uses. Verified that ALWAYS survives pg_dump and pg_restore intact --
        -- the dump copies values and sets the sequence, so no
        -- OVERRIDING SYSTEM VALUE is needed.
        ALTER TABLE region_esg_scores
            ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY;
        RAISE NOTICE 'attached an identity generator to a plain bigint id';
    ELSE
        RAISE EXCEPTION
            'region_esg_scores.id carries a default this migration does not '
            'recognise: %. Converting it automatically could change how ids '
            'are assigned. Refusing to continue.', v_id_default;
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
        -- is_called mirrors whether rows exist: true on a populated table, so
        -- the next value is strictly above max(id); false on an empty one, so
        -- the first value issued is the start itself rather than one past it.
        PERFORM setval(v_seq, coalesce(v_max_id, 1), v_max_id IS NOT NULL);
        RAISE NOTICE 'identity sequence aligned (max(id) = %)', coalesce(v_max_id::text, 'none');
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

    -- The one that was missing. Everything above can hold while an insert that
    -- omits id still fails, which is how a "converged" table reaches production
    -- and breaks on first write.
    IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
              LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
             WHERE a.attrelid = v_regclass AND a.attname = 'id'
               AND (a.attidentity IN ('a', 'd')
                    OR coalesce(pg_get_expr(d.adbin, d.adrelid), '') LIKE 'nextval(%')) THEN
        RAISE EXCEPTION
            'postcondition failed: id has no identity and no sequence default, '
            'so an insert omitting it would violate NOT NULL';
    END IF;
END
$migration$;
"""


def upgrade() -> None:
    op.execute(CONVERGE_SQL)


SAFE_DOWNGRADE_SQL = r"""
DO $downgrade$
DECLARE
    v_regclass oid := to_regclass('region_esg_scores');
    v_rows     bigint;
    v_pk       text[];
    v_bad      text[];
    v_deps     text[];
    v_fks      int;
BEGIN
    IF v_regclass IS NULL THEN
        RAISE NOTICE 'region_esg_scores is absent; nothing to do';
        RETURN;
    END IF;

    SELECT count(*) INTO v_rows FROM region_esg_scores;
    IF v_rows > 0 THEN
        RAISE EXCEPTION
            'region_esg_scores holds % row(s). Alembic cannot tell whether this '
            'table was created by the upgrade or converted from data that '
            'predates it, so dropping it could destroy production records. '
            'Refusing. Take a backup and drop it manually if that is intended.',
            v_rows;
    END IF;

    SELECT array_agg(a.attname ORDER BY a.attname) INTO v_pk
      FROM pg_constraint con
      JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = ANY (con.conkey)
     WHERE con.conrelid = v_regclass AND con.contype = 'p';
    -- v_pk is declared text[]; array_agg(attname) is name[] and is coerced into
    -- it on assignment, so the literal must stay text[] rather than be cast.
    IF v_pk IS DISTINCT FROM ARRAY['id'] THEN
        RAISE EXCEPTION
            'region_esg_scores does not carry the canonical primary key on id '
            '(found %); this is not the schema the upgrade produced. Refusing.', v_pk;
    END IF;

    SELECT array_agg(format('%s is %s', a.attname, format_type(a.atttypid, NULL)) ORDER BY a.attname)
      INTO v_bad
      FROM pg_attribute a
      JOIN (VALUES ('region_code', 'character varying'),
                   ('env_score', 'double precision'),
                   ('social_score', 'double precision'),
                   ('gov_score', 'double precision'),
                   ('total_score', 'double precision'),
                   ('confidence', 'double precision'),
                   ('sources_count', 'integer'),
                   ('signals_used', 'integer'),
                   ('updated_at', 'timestamp with time zone')
           ) AS want(name, typ) ON want.name = a.attname
     WHERE a.attrelid = v_regclass AND a.attnum > 0 AND NOT a.attisdropped
       AND format_type(a.atttypid, NULL) <> want.typ;
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION
            'region_esg_scores has non-canonical column type(s): %; this is not '
            'the schema the upgrade produced. Refusing.', v_bad;
    END IF;

    SELECT array_agg(DISTINCT dep.relname || ' (' || dep.relkind::text || ')')
      INTO v_deps
      FROM pg_depend d
      JOIN pg_rewrite rw ON rw.oid = d.objid
      JOIN pg_class dep  ON dep.oid = rw.ev_class
     WHERE d.refobjid = v_regclass AND dep.oid <> v_regclass;
    IF v_deps IS NOT NULL THEN
        RAISE EXCEPTION
            'region_esg_scores still has dependent object(s): %. Refusing.', v_deps;
    END IF;

    SELECT count(*) INTO v_fks
      FROM pg_constraint WHERE confrelid = v_regclass AND contype = 'f';
    IF v_fks > 0 THEN
        RAISE EXCEPTION
            'region_esg_scores is referenced by % foreign key(s). Refusing.', v_fks;
    END IF;

    DROP INDEX IF EXISTS ix_region_esg_scores_region_code;
    DROP TABLE region_esg_scores;
    RAISE NOTICE 'dropped an empty, canonical region_esg_scores';
END
$downgrade$;
"""


def downgrade() -> None:
    """Refuse to roll back over data rather than dropping it.

    The upgrade both creates this table on an empty database and converges one
    that already exists, and Alembic records no provenance, so after the fact
    there is no way to tell which happened. An unconditional DROP TABLE would
    therefore destroy rows this migration never created.

    The drop proceeds only when the table is empty, carries exactly the schema
    the upgrade produces, and has no dependents or foreign keys. Anything else
    raises. Rolling back a populated database is a manual operation with its own
    backup and restore plan.

    No attempt is made to reverse the type conversion: double precision back to
    real is lossy, and moving the primary key back to region_code would undo the
    alignment with RegionESGScore.
    """
    op.execute(SAFE_DOWNGRADE_SQL)
