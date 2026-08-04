"""A migrated database per test, and an alembic run that cannot fail quietly.

Not named `test_*`, so pytest imports it rather than collecting it.

The failure this exists to prevent: the first version of the write-path test ran
`python3 -m alembic upgrade head >/dev/null 2>&1` and checked nothing. In CI the
job had no alembic installed at all, so the migration never ran, the table never
existed, every statement afterwards failed silently, and all ten assertions
reported a mismatch -- ten failures that said nothing about the code under test.
`_migrate` asserts the return code and puts alembic's own stderr in the message.
"""
import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# PostgreSQL only. The migrations use PostgreSQL types and the constraints under
# test are PostgreSQL CHECKs; running this against SQLite would prove something
# about a database nobody deploys.
DATABASE_URL = os.getenv("DATABASE_URL", "")
requires_postgres = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs a PostgreSQL DATABASE_URL; the migrations are PostgreSQL-specific",
)


def _migrate(url):
    result = subprocess.run(
        # Through this interpreter, not whatever `alembic` PATH resolves to: the
        # console script may be absent, and if present it can belong to a
        # different environment than the one running the tests.
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": url.render_as_string(hide_password=False)},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        "alembic upgrade head failed, so nothing below tests what it names:\n"
        + result.stdout
        + result.stderr
    )


@pytest.fixture
def scratch_db():
    """(engine, url) for a fresh database at head. Dropped afterwards."""
    url = make_url(DATABASE_URL)
    name = "sora_period_%s" % uuid.uuid4().hex[:12]
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text('CREATE DATABASE "%s"' % name))

    scratch = url.set(database=name)
    engine = None
    try:
        _migrate(scratch)
        engine = create_engine(scratch)
        yield engine, scratch
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as conn:
            # Anything still connected would make the drop fail and leak the
            # database into the next run.
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": name},
            )
            conn.execute(text('DROP DATABASE IF EXISTS "%s"' % name))
        admin.dispose()
