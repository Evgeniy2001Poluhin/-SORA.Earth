"""Alembic must be able to build the whole schema by itself.

`alembic upgrade head` is the documented way to provision a database. If a table
is declared in the models but created by no migration, that command still exits
0 and the database is quietly incomplete -- the failure surfaces later, somewhere
else, as a missing relation.

The check owns its database. It creates a scratch one, migrates it in a
subprocess, and inspects the result, so nothing the test process happened to
import can create a table and make the schema look complete. Importing
`app.database` alone is safe -- it defines `init_db()` without calling it -- but
relying on that would make this test depend on an import order it does not
control.

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


def _scratch_database():
    """A fresh database, dropped afterwards whatever happens."""
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


@requires_postgres
def test_alembic_head_creates_every_declared_table():
    from app.database import Base

    admin, name, scratch_url = _scratch_database()
    try:
        result = subprocess.run(
            # Through this interpreter, not whatever `alembic` PATH resolves to:
            # the console script may be absent, and if it is present it can belong
            # to a different environment than the one running the tests.
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=REPO_ROOT,
            env={**os.environ, "DATABASE_URL": scratch_url.render_as_string(hide_password=False)},
            capture_output=True, text=True, timeout=300,
        )
        assert result.returncode == 0, (
            "alembic upgrade head failed on a clean database:\n%s" % result.stderr[-2000:]
        )

        engine = create_engine(scratch_url)
        with engine.connect() as conn:
            migrated = {
                row[0] for row in conn.execute(text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"))
            }
        engine.dispose()
    finally:
        _drop(admin, name)

    declared = set(Base.metadata.tables)
    missing = sorted(declared - migrated)

    assert not missing, (
        "declared in app/database.py but created by no migration: %s\n"
        "`alembic upgrade head` exits 0 and leaves these absent -- see issue #51."
        % ", ".join(missing)
    )
