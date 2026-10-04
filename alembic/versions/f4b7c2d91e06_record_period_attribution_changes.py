"""record period attribution changes

Issue #164.  ``country_indicator_history`` keeps value and fetched_at
immutable, but period corrections used to overwrite as_of_date and the
period_* provenance columns in place.  A backtest run before a correction
could therefore not be reproduced afterwards.

Keep the current columns as the materialized latest attribution for existing
readers, and append every change to a separate immutable audit table in the
same transaction.  The audit row contains both states and enough identity to
remain intelligible even if the source row is later pruned.

Revision ID: f4b7c2d91e06
Revises: e8f9a1b2c3d4
"""

from alembic import op


revision = "f4b7c2d91e06"
down_revision = "e8f9a1b2c3d4"
branch_labels = None
depends_on = None


PERIOD_COLUMNS = (
    "as_of_date",
    "period_status",
    "period_run_id",
    "period_method",
    "period_rule_version",
    "period_candidates",
    "period_source_vintage",
    "period_response_sha256",
    "period_resolved_at",
)


UPGRADE_SQL = r"""
CREATE TABLE IF NOT EXISTS country_indicator_period_history (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    history_row_id INTEGER NOT NULL,
    country_iso3 VARCHAR(10) NOT NULL,
    indicator_code VARCHAR(100) NOT NULL,
    source VARCHAR(50) NOT NULL,
    value DOUBLE PRECISION,
    fetched_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    changed_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    database_actor TEXT NOT NULL,

    old_as_of_date TIMESTAMP WITHOUT TIME ZONE,
    new_as_of_date TIMESTAMP WITHOUT TIME ZONE,
    old_period_status TEXT,
    new_period_status TEXT,
    old_period_run_id TEXT,
    new_period_run_id TEXT,
    old_period_method TEXT,
    new_period_method TEXT,
    old_period_rule_version TEXT,
    new_period_rule_version TEXT,
    old_period_candidates INTEGER,
    new_period_candidates INTEGER,
    old_period_source_vintage TEXT,
    new_period_source_vintage TEXT,
    old_period_response_sha256 TEXT,
    new_period_response_sha256 TEXT,
    old_period_resolved_at TIMESTAMP WITH TIME ZONE,
    new_period_resolved_at TIMESTAMP WITH TIME ZONE
);

DO $shape_guard$
DECLARE
    v_table regclass := to_regclass('country_indicator_period_history');
BEGIN
    IF v_table IS NULL THEN
        RAISE EXCEPTION 'country_indicator_period_history was not created';
    END IF;

    IF (SELECT count(*) FROM pg_attribute
         WHERE attrelid = v_table AND attnum > 0 AND NOT attisdropped) <> 27
       OR EXISTS (
            SELECT 1
              FROM (VALUES
                    ('id', 'bigint', true),
                    ('history_row_id', 'integer', true),
                    ('country_iso3', 'character varying(10)', true),
                    ('indicator_code', 'character varying(100)', true),
                    ('source', 'character varying(50)', true),
                    ('value', 'double precision', false),
                    ('fetched_at', 'timestamp without time zone', true),
                    ('changed_at', 'timestamp without time zone', true),
                    ('database_actor', 'text', true),
                    ('old_as_of_date', 'timestamp without time zone', false),
                    ('new_as_of_date', 'timestamp without time zone', false),
                    ('old_period_status', 'text', false),
                    ('new_period_status', 'text', false),
                    ('old_period_run_id', 'text', false),
                    ('new_period_run_id', 'text', false),
                    ('old_period_method', 'text', false),
                    ('new_period_method', 'text', false),
                    ('old_period_rule_version', 'text', false),
                    ('new_period_rule_version', 'text', false),
                    ('old_period_candidates', 'integer', false),
                    ('new_period_candidates', 'integer', false),
                    ('old_period_source_vintage', 'text', false),
                    ('new_period_source_vintage', 'text', false),
                    ('old_period_response_sha256', 'text', false),
                    ('new_period_response_sha256', 'text', false),
                    ('old_period_resolved_at', 'timestamp with time zone', false),
                    ('new_period_resolved_at', 'timestamp with time zone', false)
              ) AS expected(name, sql_type, required)
              LEFT JOIN pg_attribute a
                ON a.attrelid = v_table
               AND a.attname = expected.name
               AND a.attnum > 0
               AND NOT a.attisdropped
             WHERE a.attname IS NULL
                OR format_type(a.atttypid, a.atttypmod) <> expected.sql_type
                OR a.attnotnull IS DISTINCT FROM expected.required
       ) THEN
        RAISE EXCEPTION
            'country_indicator_period_history has an unrecognised shape; '
            'refusing to install audit triggers over drifted storage';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_attribute a
         WHERE a.attrelid = v_table
           AND a.attname = 'id'
           AND a.attidentity = 'a'
    ) OR NOT EXISTS (
        SELECT 1
          FROM pg_constraint c
         WHERE c.conrelid = v_table
           AND c.contype = 'p'
           AND array_length(c.conkey, 1) = 1
           AND (SELECT attnum FROM pg_attribute
                 WHERE attrelid = v_table AND attname = 'id') = ANY(c.conkey)
    ) THEN
        RAISE EXCEPTION
            'country_indicator_period_history.id must be an ALWAYS identity '
            'primary key';
    END IF;
END
$shape_guard$;

CREATE INDEX IF NOT EXISTS ix_ciph_row_changed
    ON country_indicator_period_history (history_row_id, changed_at, id);
CREATE INDEX IF NOT EXISTS ix_ciph_lookup
    ON country_indicator_period_history
       (country_iso3, indicator_code, fetched_at, changed_at);

CREATE OR REPLACE FUNCTION cih_record_period_change()
RETURNS trigger AS $$
BEGIN
    IF ROW(
        NEW.as_of_date,
        NEW.period_status,
        NEW.period_run_id,
        NEW.period_method,
        NEW.period_rule_version,
        NEW.period_candidates,
        NEW.period_source_vintage,
        NEW.period_response_sha256,
        NEW.period_resolved_at
    ) IS DISTINCT FROM ROW(
        OLD.as_of_date,
        OLD.period_status,
        OLD.period_run_id,
        OLD.period_method,
        OLD.period_rule_version,
        OLD.period_candidates,
        OLD.period_source_vintage,
        OLD.period_response_sha256,
        OLD.period_resolved_at
    ) THEN
        INSERT INTO country_indicator_period_history (
            history_row_id,
            country_iso3,
            indicator_code,
            source,
            value,
            fetched_at,
            changed_at,
            database_actor,
            old_as_of_date,
            new_as_of_date,
            old_period_status,
            new_period_status,
            old_period_run_id,
            new_period_run_id,
            old_period_method,
            new_period_method,
            old_period_rule_version,
            new_period_rule_version,
            old_period_candidates,
            new_period_candidates,
            old_period_source_vintage,
            new_period_source_vintage,
            old_period_response_sha256,
            new_period_response_sha256,
            old_period_resolved_at,
            new_period_resolved_at
        ) VALUES (
            OLD.id,
            OLD.country_iso3,
            OLD.indicator_code,
            OLD.source,
            OLD.value,
            OLD.fetched_at,
            clock_timestamp() AT TIME ZONE 'UTC',
            current_user,
            OLD.as_of_date,
            NEW.as_of_date,
            OLD.period_status,
            NEW.period_status,
            OLD.period_run_id,
            NEW.period_run_id,
            OLD.period_method,
            NEW.period_method,
            OLD.period_rule_version,
            NEW.period_rule_version,
            OLD.period_candidates,
            NEW.period_candidates,
            OLD.period_source_vintage,
            NEW.period_source_vintage,
            OLD.period_response_sha256,
            NEW.period_response_sha256,
            OLD.period_resolved_at,
            NEW.period_resolved_at
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_cih_record_period_change
    ON country_indicator_history;
CREATE TRIGGER trg_cih_record_period_change
BEFORE UPDATE ON country_indicator_history
FOR EACH ROW EXECUTE FUNCTION cih_record_period_change();

CREATE OR REPLACE FUNCTION ciph_refuse_rewrite()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'country_indicator_period_history is append-only; % is not allowed',
        TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ciph_append_only
BEFORE UPDATE OR DELETE ON country_indicator_period_history
FOR EACH ROW EXECUTE FUNCTION ciph_refuse_rewrite();
"""


DOWNGRADE_GUARD_SQL = r"""
DO $downgrade_guard$
BEGIN
    IF to_regclass('country_indicator_period_history') IS NOT NULL
       AND EXISTS (SELECT 1 FROM country_indicator_period_history) THEN
        RAISE EXCEPTION
            'refusing to drop non-empty country_indicator_period_history; '
            'downgrade would destroy period-attribution audit history';
    END IF;
END
$downgrade_guard$;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_GUARD_SQL)
    op.execute(
        "DROP TRIGGER IF EXISTS trg_cih_record_period_change "
        "ON country_indicator_history"
    )
    op.execute("DROP FUNCTION IF EXISTS cih_record_period_change()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_ciph_append_only "
        "ON country_indicator_period_history"
    )
    op.execute("DROP FUNCTION IF EXISTS ciph_refuse_rewrite()")
    op.execute("DROP TABLE IF EXISTS country_indicator_period_history")
