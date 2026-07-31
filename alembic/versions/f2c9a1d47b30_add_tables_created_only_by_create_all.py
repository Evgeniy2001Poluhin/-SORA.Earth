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

**The relation checked is a *compatible* superset, not any superset.** Everything
canonical must be present and match. What is extra is then classified, because
"extra" is not a synonym for "harmless" — enumerated over the whole space rather
than the cases that came to mind:

    tolerated                          refused
    ---------                          -------
    non-unique index                   NOT NULL column with no default
    nullable column                    unique index
    column with a default              constraint of any kind (check, fk,
    generated column                     unique, exclusion)
                                       trigger
                                       rule
                                       row-level security

The tolerated side is deliberate: an operator who adds an index to relieve a slow
query must not have their next deployment refused for it, and the ORM never names
a column it does not know about.

The refused side is measured, not assumed. An extra `NOT NULL` column with no
default makes every insert the models issue fail with a not-null violation; a
`DO INSTEAD NOTHING` rule turns DELETE into a silent no-op. Triggers, rules and
row-level security were invisible to an earlier version of this check — they
change no column, index or constraint, so nothing looked for them.

A *stray tolerated* object still passes silently: a nullable column left behind by
an abandoned change is indistinguishable here from one added on purpose. Catching
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
SELECT t.relname || ' index ' || i.relname
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
    "batch_results index batch_results_pkey unique=true primary=true method=btree partial=false cols=id",
    "batch_results index ix_batch_results_batch_id unique=true primary=false method=btree partial=false cols=batch_id",
    "batch_results index ix_batch_results_created_at unique=false primary=false method=btree partial=false cols=created_at",
    "batch_results index ix_batch_results_id unique=false primary=false method=btree partial=false cols=id",
    "forecast_history index forecast_history_pkey unique=true primary=true method=btree partial=false cols=id",
    "forecast_history index ix_forecast_history_country unique=false primary=false method=btree partial=false cols=country",
    "forecast_history index ix_forecast_history_created_at unique=false primary=false method=btree partial=false cols=created_at",
    "forecast_history index ix_forecast_history_id unique=false primary=false method=btree partial=false cols=id",
    "forecast_history index ix_forecast_history_metric unique=false primary=false method=btree partial=false cols=metric",
    "forecast_history index ix_forecast_history_model unique=false primary=false method=btree partial=false cols=model",
    "region_signals index ix_region_signals_observed_at unique=false primary=false method=btree partial=false cols=observed_at",
    "region_signals index ix_region_signals_region_code unique=false primary=false method=btree partial=false cols=region_code",
    "region_signals index ix_region_signals_source unique=false primary=false method=btree partial=false cols=source",
    "region_signals index region_signals_pkey unique=true primary=true method=btree partial=false cols=id",
    "retrain_log index ix_retrain_log_finished_at unique=false primary=false method=btree partial=false cols=finished_at",
    "retrain_log index ix_retrain_log_id unique=false primary=false method=btree partial=false cols=id",
    "retrain_log index ix_retrain_log_started_at unique=false primary=false method=btree partial=false cols=started_at",
    "retrain_log index ix_retrain_log_status unique=false primary=false method=btree partial=false cols=status",
    "retrain_log index ix_retrain_log_trigger_source unique=false primary=false method=btree partial=false cols=trigger_source",
    "retrain_log index retrain_log_pkey unique=true primary=true method=btree partial=false cols=id",
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


# Object kinds that can be present without being canonical. Enumerated rather
# than recalled: triggers, rules and row-level security were invisible to the
# earlier version of this check, and each of them can change what a write does
# without changing any column, index or constraint.
TRIGGER_QUERY = """
SELECT rel.relname || ' trigger ' || tg.tgname
  FROM pg_trigger tg
  JOIN pg_class rel ON rel.oid = tg.tgrelid
  JOIN pg_namespace n ON n.oid = rel.relnamespace
 WHERE n.nspname = current_schema() AND rel.relname = ANY(:tables)
   AND NOT tg.tgisinternal
"""

RULE_QUERY = """
SELECT rel.relname || ' rule ' || r.rulename
  FROM pg_rewrite r
  JOIN pg_class rel ON rel.oid = r.ev_class
  JOIN pg_namespace n ON n.oid = rel.relnamespace
 WHERE n.nspname = current_schema() AND rel.relname = ANY(:tables)
   AND r.rulename <> '_RETURN'
"""

RLS_QUERY = """
SELECT rel.relname || ' row-level security enabled'
  FROM pg_class rel
  JOIN pg_namespace n ON n.oid = rel.relnamespace
 WHERE n.nspname = current_schema() AND rel.relname = ANY(:tables)
   AND rel.relrowsecurity
"""

# The anchor table: created by 94d781c78478, long before this revision. If it
# lives somewhere other than current_schema(), the unqualified CREATE statements
# here are about to build a second copy of the application's tables in the wrong
# schema -- silently, and with the application still reading the first one.
ANCHOR_TABLE = "evaluations"
ANCHOR_QUERY = """
SELECT n.nspname
  FROM pg_class rel
  JOIN pg_namespace n ON n.oid = rel.relnamespace
 WHERE rel.relname = :anchor AND rel.relkind = 'r'
   AND n.nspname NOT IN ('pg_catalog', 'information_schema')
"""


def _assert_schema_is_the_application_schema():
    """Refuse to create the tables in a schema the application does not use."""
    conn = op.get_bind()
    here = conn.execute(text("SELECT current_schema()")).scalar()
    homes = sorted(r[0] for r in conn.execute(text(ANCHOR_QUERY), {"anchor": ANCHOR_TABLE}))
    if not homes:
        # A genuinely fresh database: nothing to disagree with. The chain that
        # ran before this revision put its tables here too.
        return
    if here not in homes:
        raise RuntimeError(
            "f2c9a1d47b30 refuses to run: current_schema() is %r, but the "
            "application's %s table lives in %s. The CREATE statements in this "
            "revision are unqualified, so they would build a second copy of these "
            "tables in %r while the application keeps reading %s.\n"
            "Set search_path so that the application's schema comes first, then "
            "run the migration again."
            % (here, ANCHOR_TABLE, ", ".join(repr(h) for h in homes), here, homes[0])
        )


def _classify_extra(label, entry):
    """Is this extra object safe to leave alone, or must it stop the migration?

    Enumerated over the whole space rather than the cases that came to mind:

        index, non-unique          tolerated -- an operator's performance index
        column, nullable           tolerated -- the ORM simply never writes it
        column, with a default     tolerated -- likewise
        column, generated          tolerated -- likewise
        column, NOT NULL, no default   REFUSED -- measured: every ORM insert
                                       fails with a not-null violation
        index, unique              REFUSED -- rejects rows the models allow
        constraint, any kind       REFUSED -- check, foreign key, unique and
                                       exclusion all reject rows the models allow
        trigger                    REFUSED -- can rewrite or reject a write
        rule                       REFUSED -- measured: a DO INSTEAD NOTHING rule
                                       turns DELETE into a silent no-op
        row-level security         REFUSED -- hides rows from the application
    """
    if label == "column":
        # "table name type notnull=<bool> default=<none|sequence|expr>"
        notnull = " notnull=true" in entry
        has_default = " default=none" not in entry
        return "tolerated" if (not notnull or has_default) else "refused"
    if label == "index":
        return "refused" if " unique=true" in entry else "tolerated"
    return "refused"


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
        #
        # Every entry begins with "<table> ", which is why all three inventories
        # are phrased that way. They were not: the index query led with the index
        # name, so this filter selected nothing and the index comparison verified
        # nothing at all. It stayed green because extras were tolerated, which
        # made "expected and present" and "unexpected but harmless"
        # indistinguishable from outside. test_these_must_be_refused now carries
        # a case per limb, so a limb that verifies nothing fails.
        expected = {e for e in whole if e.startswith(prefixes)}
        actual = {row[0] for row in conn.execute(text(query), {"tables": list(tables)})}
        for entry in sorted(expected - actual):
            problems.append("missing %s: %s" % (label, entry))
        for entry in sorted(actual - expected):
            if _classify_extra(label, entry) == "refused":
                problems.append("unexpected %s, and not a safe one: %s" % (label, entry))
            else:
                extras.append("unexpected %s (tolerated): %s" % (label, entry))

    # Kinds that are never canonical here, so any occurrence is an extra. None of
    # them changes a column, an index or a constraint, which is exactly why the
    # earlier version of this check could not see them at all.
    for label, query in (
        ("trigger", TRIGGER_QUERY),
        ("rule", RULE_QUERY),
        ("row-level security", RLS_QUERY),
    ):
        for row in conn.execute(text(query), {"tables": list(tables)}):
            problems.append("unexpected %s: %s" % (label, row[0]))

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
    # Before anything: are we even in the schema the application uses? The CREATE
    # statements below are unqualified and follow search_path wherever it points.
    _assert_schema_is_the_application_schema()

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
