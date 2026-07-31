"""Alembic must be able to build the whole schema by itself, and correctly.

`alembic upgrade head` is the documented way to provision a database. If a table
is declared in the models but created by no migration, that command still exits
0 and the database is quietly incomplete -- the failure surfaces later, somewhere
else, as a missing relation.

Each check owns its database. It creates a scratch one, migrates it in a
subprocess, and inspects the result, so nothing the test process happened to
import can create a table and make the schema look complete. Importing
`app.database` alone is safe -- it defines `init_db()` without calling it -- but
relying on that would make these tests depend on an import order they do not
control.

**Direction.** `test_alembic_head_creates_every_table_declared_in_the_models`
asserts models ⊆ schema, and deliberately not the converse: migrations
legitimately create objects with no model behind them -- `regional_esg_snapshot`
is a view in exactly that position. Nothing here would notice a table that
migrations create and no model declares. Closing that direction needs a decision
about which unmodelled objects are intended, which is open in #51.

The catalogue is queried through pg_* rather than information_schema, because
`information_schema.columns.data_type` reports `character varying` with the
length dropped -- VARCHAR(50) and VARCHAR(10) compare equal there.

Structured fields, never rendered DDL. `pg_get_indexdef` and `pg_get_expr` carry
schema qualification that depends on the session's search_path, so comparing
their text makes an identical schema look different. The schema is
`current_schema()` for the same reason: the migration's CREATE statements are
unqualified and land wherever the search_path puts them.

See issue #51.
"""

import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# PostgreSQL only. The migrations use PostgreSQL types, and running them against
# SQLite would prove something about a database nobody deploys.
DATABASE_URL = os.getenv("DATABASE_URL", "")
requires_postgres = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs a PostgreSQL DATABASE_URL; the migrations are PostgreSQL-specific",
)

# The four tables the revision under test is about.
SUBJECT_TABLES = ["batch_results", "forecast_history", "region_signals", "retrain_log"]

COLUMNS_SQL = """
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

INDEXES_SQL = """
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

# Every constraint kind: primary keys, foreign keys, unique and check alike.
# These four tables carry only primary keys today; the query does not assume it,
# so a constraint present on one side and not the other would show up.
CONSTRAINTS_SQL = """
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

SEQUENCES_SQL = """
SELECT s.relname || ' owned_by ' || rel.relname || '.' || a.attname
  FROM pg_class s
  JOIN pg_depend dep ON dep.objid = s.oid AND dep.classid = 'pg_class'::regclass
  JOIN pg_class rel ON rel.oid = dep.refobjid
  JOIN pg_attribute a ON a.attrelid = dep.refobjid AND a.attnum = dep.refobjsubid
  JOIN pg_namespace n ON n.oid = rel.relnamespace
 WHERE s.relkind = 'S' AND n.nspname = current_schema() AND rel.relname = ANY(:tables)
"""


def _scratch():
    """A fresh database. The caller drops it."""
    url = make_url(DATABASE_URL)
    name = "sora_schema_check_%s" % uuid.uuid4().hex[:12]
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text('CREATE DATABASE "%s"' % name))
    return admin, name, url.set(database=name)


def _drop(admin, name):
    with admin.connect() as conn:
        # Anything still connected would make the drop fail and leak the database.
        conn.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :n AND pid <> pg_backend_pid()"), {"n": name})
        conn.execute(text('DROP DATABASE IF EXISTS "%s"' % name))
    admin.dispose()


def _alembic(url, *args):
    return subprocess.run(
        # Through this interpreter, not whatever `alembic` PATH resolves to: the
        # console script may be absent, and if it is present it can belong to a
        # different environment than the one running the tests.
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": url.render_as_string(hide_password=False)},
        capture_output=True, text=True, timeout=300,
    )


def _inventory(url, tables):
    """Everything that makes two schemas the same one."""
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return {
                label: sorted(r[0] for r in conn.execute(text(sql), {"tables": tables}))
                for label, sql in (
                    ("columns", COLUMNS_SQL),
                    ("indexes", INDEXES_SQL),
                    ("constraints", CONSTRAINTS_SQL),
                    ("sequences", SEQUENCES_SQL),
                )
            }
    finally:
        engine.dispose()


@requires_postgres
def test_alembic_head_creates_every_table_declared_in_the_models():
    """models is a subset of the schema. See the module docstring on the converse."""
    from app.database import Base

    admin, name, url = _scratch()
    try:
        result = _alembic(url, "upgrade", "head")
        assert result.returncode == 0, (
            "alembic upgrade head failed on a clean database:\n%s" % result.stderr[-2000:]
        )
        engine = create_engine(url)
        with engine.connect() as conn:
            migrated = {
                row[0] for row in conn.execute(text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"))
            }
        engine.dispose()
    finally:
        _drop(admin, name)

    missing = sorted(set(Base.metadata.tables) - migrated)
    assert not missing, (
        "declared in app/database.py but created by no migration: %s\n"
        "`alembic upgrade head` exits 0 and leaves these absent -- see issue #51."
        % ", ".join(missing)
    )


@requires_postgres
def test_both_paths_to_head_produce_the_same_schema():
    """A fresh install and a converged deployment must be indistinguishable.

    The revision creates with IF NOT EXISTS, so on a deployment where
    create_all() already built these tables it creates nothing at all. That the
    two populations then agree is the claim -- asserted over columns with their
    full type modifiers, indexes, every constraint kind, and owned sequences.
    """
    fresh_admin, fresh_name, fresh_url = _scratch()
    conv_admin, conv_name, conv_url = _scratch()
    try:
        assert _alembic(fresh_url, "upgrade", "head").returncode == 0

        # The other path: stop before the revision, let the models build the
        # tables the way production got them, then run the revision over them.
        assert _alembic(conv_url, "upgrade", "e3f8a7c15d92").returncode == 0
        built = subprocess.run(
            [sys.executable, "-c",
             "import app.database as d; "
             "d.Base.metadata.create_all(bind=d.engine, checkfirst=True)"],
            cwd=REPO_ROOT,
            env={**os.environ, "DATABASE_URL": conv_url.render_as_string(hide_password=False)},
            capture_output=True, text=True, timeout=300,
        )
        assert built.returncode == 0, built.stderr[-2000:]

        # A row written before the upgrade, so data loss would show.
        engine = create_engine(conv_url)
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO retrain_log (started_at, status) VALUES (now(), 'pre-upgrade')"))
        engine.dispose()

        upgraded = _alembic(conv_url, "upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stderr[-2000:]

        engine = create_engine(conv_url)
        with engine.connect() as conn:
            survived = conn.execute(text(
                "SELECT count(*) FROM retrain_log WHERE status = 'pre-upgrade'")).scalar()
        engine.dispose()
        assert survived == 1, "the row written before the upgrade did not survive it"

        fresh = _inventory(fresh_url, SUBJECT_TABLES)
        converged = _inventory(conv_url, SUBJECT_TABLES)
    finally:
        _drop(fresh_admin, fresh_name)
        _drop(conv_admin, conv_name)

    for label in ("columns", "indexes", "constraints", "sequences"):
        # An empty comparison passes trivially and would hide a broken query.
        assert fresh[label], "the comparison found no %s at all -- it proves nothing" % label
        assert fresh[label] == converged[label], (
            "%s differ between a fresh install and a converged deployment:\n"
            "  only in fresh:     %s\n"
            "  only in converged: %s"
            % (label,
               sorted(set(fresh[label]) - set(converged[label])),
               sorted(set(converged[label]) - set(fresh[label])))
        )


def _canonical_then(url, *statements):
    """Reach head, rewind the *recorded* revision only, mutate, and re-run.

    `alembic stamp` moves the version row without touching the schema, so the
    revision runs again over tables that are genuinely canonical apart from the
    mutation under test. Building those tables by hand instead is how an earlier
    version of this file tested a shape that was subtly wrong everywhere, which
    made every case look refused for the wrong reason.
    """
    assert _alembic(url, "upgrade", "head").returncode == 0
    assert _alembic(url, "stamp", "e3f8a7c15d92").returncode == 0
    if statements:
        engine = create_engine(url, isolation_level="AUTOCOMMIT")
        with engine.connect() as conn:
            for statement in statements:
                conn.execute(text(statement))
        engine.dispose()
    return _alembic(url, "upgrade", "head")


@requires_postgres
def test_an_unmutated_canonical_schema_is_accepted():
    """The control for every case below: with no mutation, the revision passes.

    Without this, a refusal in the other tests would not distinguish "the check
    caught the mutation" from "the check refuses this setup regardless".
    """
    admin, name, url = _scratch()
    try:
        result = _canonical_then(url)
    finally:
        _drop(admin, name)
    assert result.returncode == 0, (result.stderr + result.stdout)[-2000:]


@requires_postgres
@pytest.mark.parametrize("what,statement", [
    ("a canonical index removed", "DROP INDEX ix_retrain_log_status"),
    ("a canonical column removed", "ALTER TABLE retrain_log DROP COLUMN message"),
    ("a canonical column retyped", "ALTER TABLE retrain_log ALTER COLUMN status TYPE varchar(10)"),
    ("a canonical column made nullable", "ALTER TABLE retrain_log ALTER COLUMN status DROP NOT NULL"),
    ("an extra NOT NULL column with no default",
     "ALTER TABLE retrain_log ADD COLUMN tenant_id integer NOT NULL"),
    ("an extra unique index", "CREATE UNIQUE INDEX ix_x ON retrain_log (status)"),
    ("an extra check constraint",
     "ALTER TABLE retrain_log ADD CONSTRAINT c_x CHECK (status <> 'x')"),
    ("a trigger", "CREATE OR REPLACE FUNCTION f_x() RETURNS trigger AS $$ BEGIN RETURN NEW; END; $$ LANGUAGE plpgsql;"
                  "CREATE TRIGGER t_x BEFORE INSERT ON retrain_log FOR EACH ROW EXECUTE FUNCTION f_x()"),
    ("a rule", "CREATE RULE r_x AS ON DELETE TO retrain_log DO INSTEAD NOTHING"),
    ("row-level security", "ALTER TABLE retrain_log ENABLE ROW LEVEL SECURITY"),
])
def test_these_must_be_refused(what, statement):
    """One negative control per limb of the check.

    Each of these changes what a write does or what the models can rely on. A
    limb with no case that fails when it is removed is decoration: the index
    comparison was exactly that for several commits -- its filter selected
    nothing, so it verified nothing, and the suite stayed green because an
    unverified index and a tolerated extra looked the same from outside.
    """
    admin, name, url = _scratch()
    try:
        result = _canonical_then(url, statement)
        current = _alembic(url, "current")
    finally:
        _drop(admin, name)

    assert result.returncode != 0, "%s was accepted" % what
    assert "refuses to complete" in (result.stderr + result.stdout), (
        "%s failed, but not with the diagnosis:\n%s" % (what, (result.stderr + result.stdout)[-1500:])
    )
    assert "e3f8a7c15d92" in current.stdout, "the revision was recorded anyway"


@requires_postgres
@pytest.mark.parametrize("what,statement", [
    ("an operator's performance index", "CREATE INDEX ix_perf ON retrain_log (job_name)"),
    ("an extra nullable column", "ALTER TABLE retrain_log ADD COLUMN note text"),
    ("an extra column with a default",
     "ALTER TABLE retrain_log ADD COLUMN seen integer NOT NULL DEFAULT 0"),
])
def test_these_must_be_tolerated(what, statement):
    """The other half. A check that refuses everything is not discriminating.

    None of these can break a write the models make: the ORM never names the
    column, and a non-unique index rejects nothing. Refusing them would block a
    correct deployment over an operator's own maintenance.
    """
    admin, name, url = _scratch()
    try:
        result = _canonical_then(url, statement)
    finally:
        _drop(admin, name)

    assert result.returncode == 0, (
        "%s was refused:\n%s" % (what, (result.stderr + result.stdout)[-1500:])
    )


@requires_postgres
def test_the_revision_refuses_a_table_that_drifted_from_the_models():
    """IF NOT EXISTS matches on name alone, so creation cannot be the guarantee.

    Given a retrain_log whose columns and index disagree with the models, the
    revision must refuse rather than record itself over a schema the database
    does not have.
    """
    admin, name, url = _scratch()
    try:
        assert _alembic(url, "upgrade", "e3f8a7c15d92").returncode == 0

        engine = create_engine(url)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE retrain_log (id SERIAL PRIMARY KEY, status TEXT)"))
            conn.execute(text("CREATE INDEX ix_retrain_log_status ON retrain_log (id)"))
        engine.dispose()

        result = _alembic(url, "upgrade", "head")
        current = _alembic(url, "current")
    finally:
        _drop(admin, name)

    assert result.returncode != 0, "the revision completed over a drifted table"
    output = result.stderr + result.stdout
    assert "refuses to complete" in output, (
        "it failed, but not with the diagnosis -- the operator learns nothing:\n%s"
        % output[-2000:]
    )
    # Named specifically, so the message is actionable rather than merely angry.
    assert "retrain_log started_at" in output, "the missing column is not named"
    assert "ix_retrain_log_status" in output, "the wrong index is not named"
    # And the recorded revision must not have moved.
    assert "e3f8a7c15d92" in current.stdout, (
        "the revision was recorded despite refusing: %s" % current.stdout
    )


@requires_postgres
def test_a_non_default_search_path_does_not_produce_a_false_refusal():
    """The verification must find the tables where the migration actually put them.

    The CREATE statements are unqualified, so they land in whatever schema the
    search_path puts first. An earlier version of the check looked in 'public'
    regardless: with search_path = 'app_schema, public' the tables were created in
    app_schema and the revision reported all 42 columns missing, refusing a
    deployment that was in fact correct. A false refusal blocks a good release,
    which is worse than the silent acceptance the check exists to prevent.
    """
    admin, name, url = _scratch()
    try:
        engine = create_engine(url, isolation_level="AUTOCOMMIT")
        with engine.connect() as conn:
            conn.execute(text("CREATE SCHEMA app_schema"))
            conn.execute(text('ALTER DATABASE "%s" SET search_path TO app_schema, public' % name))
        engine.dispose()

        result = _alembic(url, "upgrade", "head")
        current = _alembic(url, "current")

        engine = create_engine(url)
        with engine.connect() as conn:
            landed = conn.execute(text(
                "SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relname = 'retrain_log' AND c.relkind = 'r'")).scalar()
        engine.dispose()
    finally:
        _drop(admin, name)

    assert result.returncode == 0, (
        "a non-default search_path was refused:\n%s" % (result.stderr + result.stdout)[-2000:]
    )
    assert "f2c9a1d47b30" in current.stdout, "did not reach head: %s" % current.stdout
    # The premise of the test: the tables really did land somewhere other than
    # public, so passing is not just the default case in disguise.
    assert landed == "app_schema", (
        "the tables landed in %r, so this test did not exercise a non-default "
        "search_path at all" % landed
    )
