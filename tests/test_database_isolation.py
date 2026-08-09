"""The suite must not run against a database that lives in the repository.

`app/database.py` defaults `DATABASE_URL` to `data/sora.db` and builds the
engine when it is imported. That file survived between runs, and
`create_all(checkfirst=True)` creates missing *tables* but never alters an
existing one -- so a column added to a model did not reach the tests at all.
The suite kept using a schema some earlier session had built, and the symptom
was `no such column` raised from inside persistence, which reads as a bug in
the code under test rather than as a stale fixture.

It is a quiet failure in both directions: the tests can also *write* to that
file, so a run leaves state behind for the next one.

What is asserted here is the property, not the mechanism -- the engine's
database is somewhere disposable, and it is not in the working tree. A test
that checked for `mkdtemp` instead would pass just as happily against a fixture
that had stopped isolating anything.
"""
import os
from pathlib import Path

import pytest

from app.database import engine


REPO_ROOT = Path(__file__).resolve().parents[1]


def _database_path():
    """The file the engine is bound to, or None for a server database."""
    if engine.url.get_backend_name() != "sqlite":
        return None
    database = engine.url.database
    if not database or database == ":memory:":
        return None
    return Path(database).resolve()


def test_the_database_is_not_inside_the_repository():
    """The whole point, stated once."""
    path = _database_path()
    if path is None:
        pytest.skip("not a file-backed sqlite database; nothing to locate")

    assert REPO_ROOT not in path.parents, (
        f"the suite is bound to {path}, inside the working tree. A schema built "
        f"by an earlier run would be reused, and a model change would silently "
        f"not reach these tests"
    )


def test_the_repository_database_is_not_created_by_running_the_suite():
    """Absence of the side effect, not just of the binding.

    Checked separately because the two can come apart: something could point
    the engine elsewhere and still touch `data/sora.db` on the way past.
    """
    assert not (REPO_ROOT / "data" / "sora.db").exists() or _database_path() != (
        REPO_ROOT / "data" / "sora.db"), (
        "the suite is using the repository's own database file"
    )


def test_the_schema_matches_the_models_rather_than_an_older_run():
    """The failure this isolation exists to prevent, asserted directly.

    Every mapped column has to exist in the live schema. Against a database
    carried over from a previous session this is exactly what fails, and it
    fails naming the column -- which is the information the original symptom
    buried.
    """
    from sqlalchemy import inspect

    from app.database import EnvironmentalObservation

    live = {c["name"] for c in inspect(engine).get_columns(
        EnvironmentalObservation.__tablename__)}
    declared = {c.name for c in EnvironmentalObservation.__table__.columns}

    assert declared <= live, (
        f"the live schema is missing {sorted(declared - live)}; it was built "
        f"before those columns were declared"
    )


def test_the_isolation_is_not_applied_when_a_database_is_chosen_explicitly():
    """CI points DATABASE_URL at PostgreSQL after `alembic upgrade head`.

    Overriding that would move the whole suite onto SQLite and stop testing the
    database production runs -- a much larger false result than the one being
    fixed. Asserted through the environment rather than by calling the helper,
    since what matters is the state the suite ended up in.
    """
    url = os.environ.get("DATABASE_URL", "")

    assert url, "DATABASE_URL is unset; the suite has no database of its own"
    if not url.startswith("sqlite"):
        assert "sora-tests-" not in url, (
            "an explicitly configured database was replaced by the temporary one"
        )
