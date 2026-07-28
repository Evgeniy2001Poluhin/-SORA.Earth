"""converge region_esg_scores on databases already past b7c1e4a92f30

b7c1e4a92f30 creates region_esg_scores and converges legacy shapes onto the
ORM schema, but it is inserted between 31e5cc432377 and a1b2c3d4e5f6 — behind
the point any existing deployment has already reached. Alembic applies only
the revisions between the recorded version and the target, and treats every
ancestor of the recorded version as applied. A database that is already at
0b0ff6d1594e therefore never executes b7c1e4a92f30: it is marked applied
without having run.

Production is at 0b0ff6d1594e and carries the legacy shape that predates the
migration chain, created by Base.metadata.create_all() rather than by any
revision:

    region_code   text, and the PRIMARY KEY
    env_score, social_score, gov_score, total_score, confidence
                  real
    id            bigint, present via cb5a035aed2f but not the primary key

RegionESGScore expects the primary key on id, region_code as VARCHAR(10) with
a unique index, and the score columns as double precision. Without this
revision that divergence is permanent: fresh installs get the canonical schema
from b7c1e4a92f30 while existing deployments keep the legacy one forever, and
nothing in the chain would ever reconcile them.

This revision sits after the current head, so it does run on those databases,
and executes the same convergence. The statement below is a verbatim copy of
CONVERGE_SQL in b7c1e4a92f30 — the two must agree, or a converged deployment
and a fresh install would land on different schemas, which is the very defect
being closed. tests/test_migration_converge_on_head.py asserts they are
byte-identical.

The SQL is copied rather than imported. Alembic revisions are frozen history:
importing across them would let a later edit to b7c1e4a92f30 silently change
what this revision does on a database that has already run it.

Because the block is idempotent and shape-aware, the two orderings both land
on the canonical schema:

    fresh install    b7c1e4a92f30 creates the canonical table, this revision
                     then finds it already canonical and does nothing
    existing deploy  b7c1e4a92f30 never runs, this revision converts the
                     legacy table in place, preserving its rows

Revision ID: e3f8a7c15d92
Revises: 0b0ff6d1594e
Create Date: 2026-07-27 23:40:00.000000

"""
from alembic import op


revision = 'e3f8a7c15d92'
down_revision = '0b0ff6d1594e'
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
    """Deliberately inert.

    This revision creates nothing and drops nothing: it only converges a table
    that already existed. There is no safe inverse. Widening real to double
    precision is not reversible without loss, and moving the primary key back
    to region_code would undo the alignment with RegionESGScore that the whole
    chain exists to establish.

    Dropping the table here would be actively destructive. On the deployments
    this revision targets, region_esg_scores holds rows that predate the
    migration chain entirely, so a drop would destroy data no revision ever
    created.

    Downgrading past this point therefore leaves the converged schema in place.
    That is intentional: the schema it leaves behind is the one RegionESGScore
    expects, and it stays compatible with every earlier revision in the chain,
    all of which address region_esg_scores by column name rather than by type.
    """
    op.execute(
        "DO $downgrade$ BEGIN RAISE NOTICE "
        "'e3f8a7c15d92 is not reversible; region_esg_scores is left converged'; "
        "END $downgrade$;"
    )
