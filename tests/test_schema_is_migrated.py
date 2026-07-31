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

INDEXES_SQL = """
SELECT indexname || ' :: ' || indexdef
  FROM pg_indexes
 WHERE schemaname = 'public' AND tablename = ANY(:tables)
"""

# Every constraint kind: primary keys, foreign keys, unique and check alike.
# These four tables carry only primary keys today; the query does not assume it,
# so a constraint present on one side and not the other would show up.
CONSTRAINTS_SQL = """
SELECT rel.relname || ' ' || c.conname || ' ' || c.contype::text
       || ' :: ' || pg_get_constraintdef(c.oid)
  FROM pg_constraint c
  JOIN pg_class rel ON rel.oid = c.conrelid
  JOIN pg_namespace n ON n.oid = rel.relnamespace
 WHERE n.nspname = 'public' AND rel.relname = ANY(:tables)
"""

SEQUENCES_SQL = """
SELECT s.relname || ' owned_by ' || rel.relname || '.' || a.attname
  FROM pg_class s
  JOIN pg_depend dep ON dep.objid = s.oid AND dep.classid = 'pg_class'::regclass
  JOIN pg_class rel ON rel.oid = dep.refobjid
  JOIN pg_attribute a ON a.attrelid = dep.refobjid AND a.attnum = dep.refobjsubid
  JOIN pg_namespace n ON n.oid = rel.relnamespace
 WHERE s.relkind = 'S' AND n.nspname = 'public' AND rel.relname = ANY(:tables)
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
    assert "retrain_log.started_at" in output, "the missing column is not named"
    assert "ix_retrain_log_status" in output, "the wrong index is not named"
    # And the recorded revision must not have moved.
    assert "e3f8a7c15d92" in current.stdout, (
        "the revision was recorded despite refusing: %s" % current.stdout
    )
