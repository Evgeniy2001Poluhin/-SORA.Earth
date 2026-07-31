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

This revision therefore **verifies what it ends up with**, against the canonical
shape frozen below: every column with its full type modifier, nullability and
default kind; every index with its uniqueness, access method, ordered columns and
whether it is partial; every constraint of every kind with its columns and
referenced table.

**The relation checked is `canonical ⊆ actual` — a compatible superset, not
equality.** Everything canonical must be present and must match. Anything extra
is reported in the message and is *not* fatal, deliberately: an operator who adds
an index to relieve a slow query must not have their next deployment refused for
it, and a migration is not the right place to enforce that no one ever did. The
fault being guarded against is a canonical object that is absent or that exists
with a different shape.

That choice has a cost worth naming: a *stray* object — a column left behind by
an abandoned change, an index nobody remembers adding — passes silently. Catching
those is inventory work against a known-good baseline, not something a revision
can decide.

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

# Structured catalogue fields, not rendered DDL.
#
# An earlier version compared pg_indexes.indexdef and pg_get_expr() output
# directly. Rendered SQL is not a stable identity: it carries schema
# qualification that depends on the session's search_path. Measured on one
# database, one PostgreSQL, the same column:
#
#     search_path = public   nextval('retrain_log_id_seq'::regclass)
#     search_path = ''       nextval('public.retrain_log_id_seq'::regclass)
#
# So a deployment that runs migrations with a non-default search_path would have
# been refused for a schema that is in fact correct -- a false refusal blocking a
# correct deployment, which is worse than the silent acceptance being fixed.
#
# (Across PostgreSQL 14, 15, 16 and 17 the rendered text was in fact identical,
# so the version was not the trigger here. search_path was.)
#
# The schema is current_schema(), not a hardcoded 'public'. The CREATE
# statements above are unqualified, so they land in whatever the search_path puts
# first; a check that looked in 'public' regardless refused a correct deployment.
# Measured with search_path = 'app_schema, public': the tables were created in
# app_schema and the verification reported all 42 columns missing.
#
# # information_schema is avoided for a different reason: its `data_type` reports
# `character varying` with the length dropped, so VARCHAR(50) and VARCHAR(10)
# compare equal there. format_type keeps the modifier, and is the canonical
# spelling of a type rather than a rendering of a statement.
COLUMN_QUERY = """
SELECT rel.relname || ' ' || a.attname || ' ' || format_type(a.atttypid, a.atttypmod)
       || ' notnull=' || a.attnotnull::text
       || ' default=' || CASE
              WHEN d.adbin IS NULL THEN 'none'
              WHEN pg_get_expr(d.adbin, d.adrelid) LIKE 'nextval(%' THEN 'sequence'
              ELSE 'expr' END
  FROM pg_attribute a
  JOIN pg_class rel ON rel.oid = a.attrelid
  JOIN pg_namespace n ON n.oid = rel.relnamespace
  LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
 WHERE n.nspname = current_schema() AND rel.relname = ANY(:tables)
   AND a.attnum > 0 AND NOT a.attisdropped
"""

INDEX_QUERY = """
SELECT i.relname || ' on ' || t.relname
       || ' unique=' || ix.indisunique::text
       || ' primary=' || ix.indisprimary::text
       || ' method=' || am.amname
       || ' partial=' || (ix.indpred IS NOT NULL)::text
       || ' cols=' || coalesce((
             SELECT string_agg(att.attname, ',' ORDER BY k.ord)
               FROM unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ord)
               JOIN pg_attribute att ON att.attrelid = t.oid AND att.attnum = k.attnum
          ), '(expression)')
  FROM pg_index ix
  JOIN pg_class i ON i.oid = ix.indexrelid
  JOIN pg_class t ON t.oid = ix.indrelid
  JOIN pg_am am ON am.oid = i.relam
  JOIN pg_namespace n ON n.oid = t.relnamespace
 WHERE n.nspname = current_schema() AND t.relname = ANY(:tables)
"""

# Every constraint kind. A check constraint's expression is the only
# representation there is, so it is included, lowercased and whitespace-collapsed;
# these tables carry none today, and the query does not assume that.
CONSTRAINT_QUERY = """
SELECT rel.relname || ' ' || c.conname || ' type=' || c.contype::text
       || ' cols=' || coalesce((
             SELECT string_agg(att.attname, ',' ORDER BY k.ord)
               FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
               JOIN pg_attribute att ON att.attrelid = c.conrelid AND att.attnum = k.attnum
          ), '-')
       || ' refs=' || coalesce(fr.relname, '-')
       || ' check=' || coalesce(
             regexp_replace(lower(pg_get_expr(c.conbin, c.conrelid)), '\\s+', ' ', 'g'), '-')
  FROM pg_constraint c
  JOIN pg_class rel ON rel.oid = c.conrelid
  JOIN pg_namespace n ON n.oid = rel.relnamespace
  LEFT JOIN pg_class fr ON fr.oid = c.confrelid
 WHERE n.nspname = current_schema() AND rel.relname = ANY(:tables)
"""

EXPECTED_COLUMNS = {
    "batch_results batch_id character varying(64) notnull=true default=none",
    "batch_results created_at timestamp without time zone notnull=true default=none",
    "batch_results duration_ms double precision notnull=false default=none",
    "batch_results error_message text notnull=false default=none",
    "batch_results failed integer notnull=true default=none",
    "batch_results id integer notnull=true default=sequence",
    "batch_results job_type character varying(50) notnull=true default=none",
    "batch_results results_json text notnull=false default=none",
    "batch_results status character varying(50) notnull=true default=none",
    "batch_results successful integer notnull=true default=none",
    "batch_results total integer notnull=true default=none",
    "batch_results trigger_source character varying(50) notnull=true default=none",
    "forecast_history confidence character varying(10) notnull=false default=none",
    "forecast_history country character varying(10) notnull=false default=none",
    "forecast_history created_at timestamp without time zone notnull=true default=none",
    "forecast_history forecast_json text notnull=true default=none",
    "forecast_history horizon integer notnull=true default=none",
    "forecast_history id integer notnull=true default=sequence",
    "forecast_history metadata_json text notnull=false default=none",
    "forecast_history metric character varying(50) notnull=true default=none",
    "forecast_history model character varying(50) notnull=true default=none",
    "region_signals created_at timestamp with time zone notnull=true default=none",
    "region_signals id integer notnull=true default=sequence",
    "region_signals metadata_json text notnull=false default=none",
    "region_signals metric character varying(64) notnull=true default=none",
    "region_signals observed_at timestamp with time zone notnull=false default=none",
    "region_signals region_code character varying(10) notnull=true default=none",
    "region_signals source character varying(32) notnull=true default=none",
    "region_signals unit character varying(32) notnull=false default=none",
    "region_signals value double precision notnull=false default=none",
    "retrain_log data_version character varying(100) notnull=false default=none",
    "retrain_log duration_sec double precision notnull=false default=none",
    "retrain_log error_message text notnull=false default=none",
    "retrain_log finished_at timestamp without time zone notnull=false default=none",
    "retrain_log id integer notnull=true default=sequence",
    "retrain_log job_name character varying(100) notnull=false default=none",
    "retrain_log message text notnull=false default=none",
    "retrain_log metrics_json text notnull=false default=none",
    "retrain_log model_version character varying(100) notnull=false default=none",
    "retrain_log started_at timestamp without time zone notnull=true default=none",
    "retrain_log status character varying(50) notnull=true default=none",
    "retrain_log trigger_source character varying(50) notnull=false default=none",
}

EXPECTED_INDEXES = {
    "batch_results_pkey on batch_results unique=true primary=true method=btree partial=false cols=id",
    "forecast_history_pkey on forecast_history unique=true primary=true method=btree partial=false cols=id",
    "ix_batch_results_batch_id on batch_results unique=true primary=false method=btree partial=false cols=batch_id",
    "ix_batch_results_created_at on batch_results unique=false primary=false method=btree partial=false cols=created_at",
    "ix_batch_results_id on batch_results unique=false primary=false method=btree partial=false cols=id",
    "ix_forecast_history_country on forecast_history unique=false primary=false method=btree partial=false cols=country",
    "ix_forecast_history_created_at on forecast_history unique=false primary=false method=btree partial=false cols=created_at",
    "ix_forecast_history_id on forecast_history unique=false primary=false method=btree partial=false cols=id",
    "ix_forecast_history_metric on forecast_history unique=false primary=false method=btree partial=false cols=metric",
    "ix_forecast_history_model on forecast_history unique=false primary=false method=btree partial=false cols=model",
    "ix_region_signals_observed_at on region_signals unique=false primary=false method=btree partial=false cols=observed_at",
    "ix_region_signals_region_code on region_signals unique=false primary=false method=btree partial=false cols=region_code",
    "ix_region_signals_source on region_signals unique=false primary=false method=btree partial=false cols=source",
    "ix_retrain_log_finished_at on retrain_log unique=false primary=false method=btree partial=false cols=finished_at",
    "ix_retrain_log_id on retrain_log unique=false primary=false method=btree partial=false cols=id",
    "ix_retrain_log_started_at on retrain_log unique=false primary=false method=btree partial=false cols=started_at",
    "ix_retrain_log_status on retrain_log unique=false primary=false method=btree partial=false cols=status",
    "ix_retrain_log_trigger_source on retrain_log unique=false primary=false method=btree partial=false cols=trigger_source",
    "region_signals_pkey on region_signals unique=true primary=true method=btree partial=false cols=id",
    "retrain_log_pkey on retrain_log unique=true primary=true method=btree partial=false cols=id",
}

EXPECTED_CONSTRAINTS = {
    "batch_results batch_results_pkey type=p cols=id refs=- check=-",
    "forecast_history forecast_history_pkey type=p cols=id refs=- check=-",
    "region_signals region_signals_pkey type=p cols=id refs=- check=-",
    "retrain_log retrain_log_pkey type=p cols=id refs=- check=-",
}


def _existing_tables():
    conn = op.get_bind()
    return sorted(
        row[0] for row in conn.execute(
            text("SELECT tablename FROM pg_tables "
                 "WHERE schemaname = current_schema() AND tablename = ANY(:tables)"),
            {"tables": list(TABLES)})
    )


def _assert_canonical(tables):
    """Refuse to record this revision over a schema that is not the canonical one."""
    if not tables:
        return
    prefixes = tuple("%s " % t for t in tables)
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
        # Every entry begins with the table name followed by a space, in all
        # three inventories, so one rule covers them.
        expected = {e for e in whole if e.startswith(prefixes)}
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
