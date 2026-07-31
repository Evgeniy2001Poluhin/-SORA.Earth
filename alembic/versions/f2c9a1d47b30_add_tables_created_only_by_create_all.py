"""create the four tables that only ever existed via create_all()

`batch_results`, `forecast_history`, `region_signals` and `retrain_log` are
declared in app/database.py but were created by no revision. They existed only
because `Base.metadata.create_all()` runs when the application is imported, so
`alembic upgrade head` on a clean database exited 0 and left them absent —
provisioning without starting the app produced a schema missing four tables,
with no error to explain it. See issue #51.

The DDL below was generated from the ORM models with SQLAlchemy's PostgreSQL
dialect and then frozen here, rather than imported from app.database. Alembic
revisions are history: importing the models would let a later edit silently
change what this revision does on a database that has already run it.

`IF NOT EXISTS` creates what is missing:

    fresh install    nothing exists; this revision creates the canonical schema
    existing deploy  create_all() already made these tables from the same
                     models; this revision finds them and creates nothing

Creating is not converging, and `IF NOT EXISTS` matches on **name alone**.
Measured on PostgreSQL 16 against a table that had drifted from the models:

    CREATE TABLE IF NOT EXISTS retrain_log (...)   NOTICE: already exists, skipping
    CREATE INDEX IF NOT EXISTS ix_retrain_log_status ON retrain_log (status)
                                                   NOTICE: already exists, skipping

    afterwards:  status is still `text`, not VARCHAR(50)
                 started_at is still absent entirely
                 ix_retrain_log_status is still on (id), not (status)

...and the migration reported success. So the creation alone would let a
divergent database reach head and claim a schema it does not have.

This revision therefore **verifies what it ends up with**. After the creation it
compares every column with its full type and modifier, every nullability, every
default, every index definition and every constraint against the canonical shape
frozen below, and raises if anything expected is absent. Extra indexes and
columns an operator added are reported but not fatal — the failure being guarded
against is a shape that silently disagrees, not a superset.

What this still does **not** do is *repair* a drifted table. It refuses, loudly,
with the differences listed, and converging those shapes is a separate revision
written when it is known which shapes exist. Refusing is the honest thing a
revision can do without that knowledge; succeeding quietly is not.

Revision ID: f2c9a1d47b30
Revises: e3f8a7c15d92
Create Date: 2026-07-31 09:40:00.000000

"""
from alembic import op
from sqlalchemy import text


revision = 'f2c9a1d47b30'
down_revision = 'e3f8a7c15d92'
branch_labels = None
depends_on = None


CREATE_SQL = r"""
CREATE TABLE IF NOT EXISTS batch_results (
    id SERIAL NOT NULL,
    batch_id VARCHAR(64) NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    job_type VARCHAR(50) NOT NULL,
    total INTEGER NOT NULL,
    successful INTEGER NOT NULL,
    failed INTEGER NOT NULL,
    duration_ms FLOAT,
    status VARCHAR(50) NOT NULL,
    error_message TEXT,
    results_json TEXT,
    trigger_source VARCHAR(50) NOT NULL,
    PRIMARY KEY (id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_batch_results_batch_id ON batch_results (batch_id);
CREATE INDEX IF NOT EXISTS ix_batch_results_created_at ON batch_results (created_at);
CREATE INDEX IF NOT EXISTS ix_batch_results_id ON batch_results (id);

CREATE TABLE IF NOT EXISTS forecast_history (
    id SERIAL NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    metric VARCHAR(50) NOT NULL,
    model VARCHAR(50) NOT NULL,
    horizon INTEGER NOT NULL,
    country VARCHAR(10),
    forecast_json TEXT NOT NULL,
    confidence VARCHAR(10),
    metadata_json TEXT,
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_forecast_history_country ON forecast_history (country);
CREATE INDEX IF NOT EXISTS ix_forecast_history_created_at ON forecast_history (created_at);
CREATE INDEX IF NOT EXISTS ix_forecast_history_id ON forecast_history (id);
CREATE INDEX IF NOT EXISTS ix_forecast_history_metric ON forecast_history (metric);
CREATE INDEX IF NOT EXISTS ix_forecast_history_model ON forecast_history (model);

CREATE TABLE IF NOT EXISTS region_signals (
    id SERIAL NOT NULL,
    region_code VARCHAR(10) NOT NULL,
    source VARCHAR(32) NOT NULL,
    metric VARCHAR(64) NOT NULL,
    value FLOAT,
    unit VARCHAR(32),
    observed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    metadata_json TEXT,
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_region_signals_observed_at ON region_signals (observed_at);
CREATE INDEX IF NOT EXISTS ix_region_signals_region_code ON region_signals (region_code);
CREATE INDEX IF NOT EXISTS ix_region_signals_source ON region_signals (source);

CREATE TABLE IF NOT EXISTS retrain_log (
    id SERIAL NOT NULL,
    started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    finished_at TIMESTAMP WITHOUT TIME ZONE,
    duration_sec FLOAT,
    status VARCHAR(50) NOT NULL,
    trigger_source VARCHAR(50),
    job_name VARCHAR(100),
    model_version VARCHAR(100),
    data_version VARCHAR(100),
    metrics_json TEXT,
    error_message TEXT,
    message TEXT,
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_retrain_log_finished_at ON retrain_log (finished_at);
CREATE INDEX IF NOT EXISTS ix_retrain_log_id ON retrain_log (id);
CREATE INDEX IF NOT EXISTS ix_retrain_log_started_at ON retrain_log (started_at);
CREATE INDEX IF NOT EXISTS ix_retrain_log_status ON retrain_log (status);
CREATE INDEX IF NOT EXISTS ix_retrain_log_trigger_source ON retrain_log (trigger_source);
"""


TABLES = ("batch_results", "forecast_history", "region_signals", "retrain_log")

# Queried through the catalogue rather than information_schema: `data_type`
# there reports `character varying` with the length dropped, so VARCHAR(50) and
# VARCHAR(10) compare equal. format_type keeps the modifier.
COLUMN_QUERY = """
SELECT rel.relname || '.' || a.attname || ' ' || format_type(a.atttypid, a.atttypmod)
       || ' null=' || (NOT a.attnotnull)::text
       || ' default=' || coalesce(pg_get_expr(d.adbin, d.adrelid), '-')
  FROM pg_attribute a
  JOIN pg_class rel ON rel.oid = a.attrelid
  JOIN pg_namespace n ON n.oid = rel.relnamespace
  LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
 WHERE n.nspname = 'public' AND rel.relname = ANY(:tables)
   AND a.attnum > 0 AND NOT a.attisdropped
"""

INDEX_QUERY = """
SELECT indexname || ' :: ' || indexdef
  FROM pg_indexes
 WHERE schemaname = 'public' AND tablename = ANY(:tables)
"""

CONSTRAINT_QUERY = """
SELECT rel.relname || ' ' || c.conname || ' ' || c.contype::text
       || ' :: ' || pg_get_constraintdef(c.oid)
  FROM pg_constraint c
  JOIN pg_class rel ON rel.oid = c.conrelid
  JOIN pg_namespace n ON n.oid = rel.relnamespace
 WHERE n.nspname = 'public' AND rel.relname = ANY(:tables)
"""

EXPECTED_COLUMNS = {
    "batch_results.batch_id character varying(64) null=false default=-",
    "batch_results.created_at timestamp without time zone null=false default=-",
    "batch_results.duration_ms double precision null=true default=-",
    "batch_results.error_message text null=true default=-",
    "batch_results.failed integer null=false default=-",
    "batch_results.id integer null=false default=nextval('batch_results_id_seq'::regclass)",
    "batch_results.job_type character varying(50) null=false default=-",
    "batch_results.results_json text null=true default=-",
    "batch_results.status character varying(50) null=false default=-",
    "batch_results.successful integer null=false default=-",
    "batch_results.total integer null=false default=-",
    "batch_results.trigger_source character varying(50) null=false default=-",
    "forecast_history.confidence character varying(10) null=true default=-",
    "forecast_history.country character varying(10) null=true default=-",
    "forecast_history.created_at timestamp without time zone null=false default=-",
    "forecast_history.forecast_json text null=false default=-",
    "forecast_history.horizon integer null=false default=-",
    "forecast_history.id integer null=false default=nextval('forecast_history_id_seq'::regclass)",
    "forecast_history.metadata_json text null=true default=-",
    "forecast_history.metric character varying(50) null=false default=-",
    "forecast_history.model character varying(50) null=false default=-",
    "region_signals.created_at timestamp with time zone null=false default=-",
    "region_signals.id integer null=false default=nextval('region_signals_id_seq'::regclass)",
    "region_signals.metadata_json text null=true default=-",
    "region_signals.metric character varying(64) null=false default=-",
    "region_signals.observed_at timestamp with time zone null=true default=-",
    "region_signals.region_code character varying(10) null=false default=-",
    "region_signals.source character varying(32) null=false default=-",
    "region_signals.unit character varying(32) null=true default=-",
    "region_signals.value double precision null=true default=-",
    "retrain_log.data_version character varying(100) null=true default=-",
    "retrain_log.duration_sec double precision null=true default=-",
    "retrain_log.error_message text null=true default=-",
    "retrain_log.finished_at timestamp without time zone null=true default=-",
    "retrain_log.id integer null=false default=nextval('retrain_log_id_seq'::regclass)",
    "retrain_log.job_name character varying(100) null=true default=-",
    "retrain_log.message text null=true default=-",
    "retrain_log.metrics_json text null=true default=-",
    "retrain_log.model_version character varying(100) null=true default=-",
    "retrain_log.started_at timestamp without time zone null=false default=-",
    "retrain_log.status character varying(50) null=false default=-",
    "retrain_log.trigger_source character varying(50) null=true default=-",
}

EXPECTED_INDEXES = {
    "batch_results_pkey :: CREATE UNIQUE INDEX batch_results_pkey ON public.batch_results USING btree (id)",
    "forecast_history_pkey :: CREATE UNIQUE INDEX forecast_history_pkey ON public.forecast_history USING btree (id)",
    "ix_batch_results_batch_id :: CREATE UNIQUE INDEX ix_batch_results_batch_id ON public.batch_results USING btree (batch_id)",
    "ix_batch_results_created_at :: CREATE INDEX ix_batch_results_created_at ON public.batch_results USING btree (created_at)",
    "ix_batch_results_id :: CREATE INDEX ix_batch_results_id ON public.batch_results USING btree (id)",
    "ix_forecast_history_country :: CREATE INDEX ix_forecast_history_country ON public.forecast_history USING btree (country)",
    "ix_forecast_history_created_at :: CREATE INDEX ix_forecast_history_created_at ON public.forecast_history USING btree (created_at)",
    "ix_forecast_history_id :: CREATE INDEX ix_forecast_history_id ON public.forecast_history USING btree (id)",
    "ix_forecast_history_metric :: CREATE INDEX ix_forecast_history_metric ON public.forecast_history USING btree (metric)",
    "ix_forecast_history_model :: CREATE INDEX ix_forecast_history_model ON public.forecast_history USING btree (model)",
    "ix_region_signals_observed_at :: CREATE INDEX ix_region_signals_observed_at ON public.region_signals USING btree (observed_at)",
    "ix_region_signals_region_code :: CREATE INDEX ix_region_signals_region_code ON public.region_signals USING btree (region_code)",
    "ix_region_signals_source :: CREATE INDEX ix_region_signals_source ON public.region_signals USING btree (source)",
    "ix_retrain_log_finished_at :: CREATE INDEX ix_retrain_log_finished_at ON public.retrain_log USING btree (finished_at)",
    "ix_retrain_log_id :: CREATE INDEX ix_retrain_log_id ON public.retrain_log USING btree (id)",
    "ix_retrain_log_started_at :: CREATE INDEX ix_retrain_log_started_at ON public.retrain_log USING btree (started_at)",
    "ix_retrain_log_status :: CREATE INDEX ix_retrain_log_status ON public.retrain_log USING btree (status)",
    "ix_retrain_log_trigger_source :: CREATE INDEX ix_retrain_log_trigger_source ON public.retrain_log USING btree (trigger_source)",
    "region_signals_pkey :: CREATE UNIQUE INDEX region_signals_pkey ON public.region_signals USING btree (id)",
    "retrain_log_pkey :: CREATE UNIQUE INDEX retrain_log_pkey ON public.retrain_log USING btree (id)",
}

EXPECTED_CONSTRAINTS = {
    "batch_results batch_results_pkey p :: PRIMARY KEY (id)",
    "forecast_history forecast_history_pkey p :: PRIMARY KEY (id)",
    "region_signals region_signals_pkey p :: PRIMARY KEY (id)",
    "retrain_log retrain_log_pkey p :: PRIMARY KEY (id)",
}


def _existing_tables():
    conn = op.get_bind()
    return sorted(
        row[0] for row in conn.execute(
            text("SELECT tablename FROM pg_tables "
                 "WHERE schemaname = 'public' AND tablename = ANY(:tables)"),
            {"tables": list(TABLES)})
    )


def _assert_canonical(tables):
    """Refuse to record this revision over a schema that is not the canonical one."""
    if not tables:
        return
    prefixes = tuple("%s." % t for t in tables)
    conn = op.get_bind()
    problems = []
    extras = []
    for label, query, whole in (
        ("column", COLUMN_QUERY, EXPECTED_COLUMNS),
        ("index", INDEX_QUERY, EXPECTED_INDEXES),
        ("constraint", CONSTRAINT_QUERY, EXPECTED_CONSTRAINTS),
    ):
        # Only the tables being checked: called before creation, the others do
        # not exist yet and their absence is the normal case, not a fault.
        if label == "column":
            expected = {e for e in whole if e.startswith(prefixes)}
        else:
            expected = {e for e in whole if any(t in e for t in tables)}
        actual = {row[0] for row in conn.execute(text(query), {"tables": list(tables)})}
        for entry in sorted(expected - actual):
            problems.append("missing %s: %s" % (label, entry))
        for entry in sorted(actual - expected):
            extras.append("unexpected %s: %s" % (label, entry))

    if problems:
        raise RuntimeError(
            "f2c9a1d47b30 refuses to complete: these tables exist but do not match the "
            "canonical schema, so recording this revision would claim a shape the "
            "database does not have.\n\n%s\n\n%s\n\n"
            "They were created by Base.metadata.create_all() from a model that has "
            "since changed. Converging them needs a revision written for the shapes "
            "actually deployed -- see issue #51."
            % ("\n".join(problems),
               "\n".join(extras) if extras else "(nothing unexpected present)")
        )


def upgrade():
    # Whatever is already here is checked *first*. Running the creation over a
    # drifted table fails deep inside the DDL instead -- measured, the index on
    # retrain_log.started_at raised `column "started_at" does not exist`, which
    # is a refusal, but one that says nothing about what is actually wrong.
    _assert_canonical(_existing_tables())

    op.execute(CREATE_SQL)

    # And again over everything, because creating is not converging: IF NOT
    # EXISTS matches on name alone, so nothing above proves the result is right.
    _assert_canonical(TABLES)


def downgrade():
    # Irreversible, and it says so rather than pretending.
    #
    # On every deployment predating this revision these tables already existed,
    # holding data, created by create_all() rather than here. Dropping them would
    # destroy data the revision never owned. Doing nothing is worse still: Alembic
    # would move alembic_version back while the tables remained, leaving the
    # recorded revision and the actual schema disagreeing -- a database that
    # believes it is at e3f8a7c15d92 while carrying f2c9a1d47b30's schema.
    #
    # So it fails, and the operator decides. Removing these tables is a separate,
    # deliberate migration, written when something actually wants them gone.
    raise RuntimeError(
        "f2c9a1d47b30 cannot be downgraded automatically. It creates tables only "
        "where they are absent, so it cannot tell the ones it created from the "
        "ones create_all() had already made -- and those hold data. Drop them "
        "explicitly if that is what you want, then `alembic stamp e3f8a7c15d92`."
    )
