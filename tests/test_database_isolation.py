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
from unittest.mock import patch

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


def test_the_repository_database_is_untouched_by_the_suite():
    """Absence of the side effect, not just of the binding.

    Checked separately because the two can come apart: something could point
    the engine elsewhere and still touch `data/sora.db` on the way past.

    The earlier version of this test could not fail when the file already
    existed -- `not exists() or engine_path != repo_db` is satisfied by the
    binding alone, so it re-asserted the previous test under a name that
    promised more. Compared here against a fingerprint taken in conftest before
    the application was imported: existence, size, mtime_ns and SHA-256.
    """
    from tests.conftest import REPO_DB, REPO_DB_BEFORE, _fingerprint

    after = _fingerprint(REPO_DB)

    assert after == REPO_DB_BEFORE, (
        f"the repository database changed while the suite ran: "
        f"{REPO_DB_BEFORE} -> {after}"
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
    """CI points TEST_DATABASE_URL at PostgreSQL after `alembic upgrade head`.

    Ignoring it would move the whole suite onto SQLite and stop testing the
    database production runs -- a much larger false result than the one being
    fixed. Asserted through the environment rather than by calling the helper,
    since what matters is the state the suite ended up in.

    Named precisely: this said `DATABASE_URL`, which is the variable the branch
    stops trusting. A description of the old contract sitting next to the test
    that enforces the new one is how the old one gets restored.
    """
    from sqlalchemy.engine import make_url

    explicit = os.environ.get("TEST_DATABASE_URL")
    if not explicit:
        pytest.skip("no explicit test database configured; nothing to honour")

    wanted = make_url(explicit)
    actual = engine.url

    # Compared field by field. `str(url)` masks the password, so two different
    # credentials render identically and the comparison would pass on a
    # database the caller did not ask for.
    # Every field, password included. An earlier version compared host, port,
    # database and username while its own comment named the password as the
    # risk -- so two different credentials against the same host would have
    # passed the test that was written to catch exactly that.
    fields = ("drivername", "username", "password", "host", "port", "database",
              "query")
    mismatched = [f for f in fields if getattr(actual, f) != getattr(wanted, f)]

    assert not mismatched, (
        f"TEST_DATABASE_URL and the engine differ on {mismatched}: asked for "
        f"{wanted.render_as_string()}, bound to {actual.render_as_string()}"
    )


# --- which database the suite is allowed to take ---------------------------


def test_an_ambient_database_url_is_not_trusted():
    """A shell holding a production URL must not become the test database.

    The first version used `os.environ.setdefault`, which keeps whatever is
    already exported. That is safe against overwriting CI's configuration and
    unsafe in the direction that matters -- the suite writes, and `create_all`
    would add missing tables to whatever it was handed.

    Asserted against the pure chooser rather than by exporting the variable and
    re-importing the application, because reproducing this case for real means
    pointing a test run at the database nobody wants it to touch.
    """
    from tests._database_url import choose_database_url

    url, temp = choose_database_url(
        {"DATABASE_URL": "postgresql://sora:pw@prod.internal:5432/sora_earth"},
        make_temp_dir=lambda: "/tmp/pretend",
    )

    assert url.startswith("sqlite:///"), (
        f"an ambient DATABASE_URL was accepted as the test database: {url}"
    )
    assert temp == "/tmp/pretend", "no temporary database was created"


def test_an_explicit_test_database_url_is_used_as_given():
    """CI passes its ephemeral PostgreSQL this way, and nothing else may.

    The temporary directory must not be created either: a stray one would mean
    the chooser did the work and then discarded it, which is how a cleanup hook
    ends up deleting something it did not make.
    """
    from tests._database_url import choose_database_url

    def _must_not_run():
        raise AssertionError("a temporary database was created despite an "
                             "explicit TEST_DATABASE_URL")

    url, temp = choose_database_url(
        {"TEST_DATABASE_URL": "postgresql://sora:sora2026@localhost:5432/sora_earth",
         "DATABASE_URL": "postgresql://sora:pw@prod.internal:5432/sora_earth"},
        make_temp_dir=_must_not_run,
    )

    assert url == "postgresql://sora:sora2026@localhost:5432/sora_earth"
    assert temp is None, "cleanup would remove a directory this run did not create"


def test_only_one_session_finish_hook_is_defined():
    """pytest calls one of them, and the other is dead code.

    A module binds a name once. A second `pytest_sessionfinish` anywhere in
    conftest silently replaces the first, and the replaced body never runs --
    which is what happened to the database cleanup: it leaked a temporary
    directory per run while everything looked green, because a cleanup that
    does not run is indistinguishable from one that succeeded.

    Parsed rather than grepped: a comment or a docstring mentioning the name
    would satisfy a substring search.
    """
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).parent / "conftest.py").read_text())
    defined = [n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "pytest_sessionfinish"]

    assert len(defined) == 1, (
        f"conftest defines pytest_sessionfinish {len(defined)} times, at lines "
        f"{[n.lineno for n in defined]}; only the last one runs"
    )


def test_the_cleanup_removes_the_directory_it_was_given(tmp_path):
    """The body that never ran, now exercised directly."""
    from tests.conftest import _cleanup_test_database

    victim = tmp_path / "sora-tests-x"
    victim.mkdir()
    (victim / "test.db").write_bytes(b"")

    _cleanup_test_database(str(victim))

    assert not victim.exists()


def test_the_engine_is_disposed_before_the_files_go(tmp_path):
    """Order matters: an open SQLite handle can keep the file alive, and the
    removal then does nothing while reporting success."""
    from unittest.mock import MagicMock

    import app.database
    from tests.conftest import _cleanup_test_database

    order = []
    victim = tmp_path / "sora-tests-y"
    victim.mkdir()

    engine = MagicMock()
    engine.dispose.side_effect = lambda: order.append("dispose")
    with patch.object(app.database, "engine", engine), \
         patch("shutil.rmtree", side_effect=lambda p: order.append("rmtree")):
        _cleanup_test_database(str(victim))

    assert order == ["dispose", "rmtree"], f"wrong order: {order}"


def test_nothing_is_removed_when_the_caller_supplied_the_database():
    """`None` means TEST_DATABASE_URL was set, so the database is not ours.

    The engine must not even be touched: disposing a caller's connection pool
    at the end of a run is a side effect nobody asked for.
    """
    from unittest.mock import MagicMock

    import app.database
    from tests.conftest import _cleanup_test_database

    engine = MagicMock()
    with patch.object(app.database, "engine", engine), \
         patch("shutil.rmtree") as rmtree:
        _cleanup_test_database(None)

    engine.dispose.assert_not_called()
    rmtree.assert_not_called()


def test_the_session_finish_hook_calls_the_cleanup():
    """Uniqueness is not enough: the one surviving hook has to be wired to it.

    Parsed, so a mention in a docstring or a comment does not satisfy it.
    """
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).parent / "conftest.py").read_text())
    hook = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "pytest_sessionfinish")
    called = {n.func.id for n in ast.walk(hook)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}

    assert "_cleanup_test_database" in called, (
        "the surviving hook never calls the cleanup, so the directory leaks "
        "exactly as it did when the hook was duplicated"
    )
