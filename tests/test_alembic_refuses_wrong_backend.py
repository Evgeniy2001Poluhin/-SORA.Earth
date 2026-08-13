"""`alembic upgrade head` names the wrong backend instead of half-building one.

env.py used to default to `sqlite:///data/sora.db` when DATABASE_URL was unset.
The migration chain is PostgreSQL-only by construction -- named CHECK
constraints, partial unique indexes, EXTRACT(EPOCH ...), to_regclass,
transactional DDL -- so that default did something worse than fail: it built
part of a schema in a stray file and then died inside a revision on a
PostgreSQL function, reporting a SQL syntax problem rather than the wrong
database.

The guard was first written *below* `from app.database import Base`, which
builds its engine from the same variable at import time. A mysql URL therefore
raised `ModuleNotFoundError: No module named 'MySQLdb'` at env.py line 15 and
never reached the check. A guard placed after the thing it guards is decoration,
and only sqlite -- which has a driver built in -- made it look like it worked.
"""
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _alembic(url):
    return subprocess.run(
        [sys.executable, "-m", "alembic", "current"],
        cwd=REPO_ROOT, env={**os.environ, "DATABASE_URL": url},
        capture_output=True, text=True, timeout=120,
    )


@pytest.mark.parametrize("url,expected", [
    ("sqlite:///./whatever.db", "sqlite"),
    ("mysql://user@host/db", "mysql"),
])
def test_a_non_postgres_url_is_refused_by_name(url, expected):
    """Named, so the message says what is wrong rather than what broke."""
    result = _alembic(url)

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert f"points at {expected}" in output, output[-800:]
    assert "PostgreSQL-specific" in output


@pytest.mark.parametrize("url,expected", [
    ("sqlite:///./whatever.db", "sqlite"),
    ("mysql://user@host/db", "mysql"),
])
def test_the_refusal_comes_before_any_driver_import(url, expected):
    """The failure mode the first version had.

    If the check sits below `from app.database import Base`, mysql dies on a
    missing DBAPI and sqlite passes straight through to the check -- so testing
    sqlite alone would have reported the guard as working.
    """
    output = _alembic(url).stdout + _alembic(url).stderr

    assert "ModuleNotFoundError" not in output, (
        "a driver import ran before the backend was checked"
    )


def test_an_empty_url_is_refused_rather_than_guessed():
    result = _alembic("")

    assert result.returncode != 0
    assert "DATABASE_URL is not set" in result.stdout + result.stderr


def test_there_is_no_sqlite_default_left_in_the_file():
    """Read as well as executed: the default is the thing being removed."""
    env_py = open(os.path.join(REPO_ROOT, "alembic", "env.py")).read()
    executable = "\n".join(
        line for line in env_py.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )

    assert "sqlite:///data/sora.db" not in executable


def test_postgres_still_works():
    """Otherwise the refusals above are satisfied by refusing everything."""
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("needs a PostgreSQL DATABASE_URL to prove the happy path")

    result = _alembic(url)

    assert result.returncode == 0, (result.stdout + result.stderr)[-800:]
