"""`get_experiment_stats` must read the database the application is configured with.

It used to open `sqlite3.connect("data/sora.db")` (#137): a hardcoded relative
path that ignored DATABASE_URL. Production runs PostgreSQL, so the call created
an empty SQLite file, found no `retrain_log` in it, and a broad `except` turned
that into the default response. `/api/v1/mlflow/stats` reported no metrics
regardless of what the real table held, and said so only through an
`_sqlite_error` field nobody read.

The relative path was a second defect: it resolves against the working
directory, so the same process read a different file depending on where it was
started.

These tests configure their own engine explicitly rather than relying on the
suite's database fixture, so they hold whatever that fixture happens to be.
"""
import os

# Before anything imports app.mlflow_tracking: the module calls
# mlflow.set_experiment() at import time, which reaches the tracking
# server. Without this the suite spends minutes on connection retries and
# fails on ConnectionRefusedError rather than on anything under test.
os.environ.setdefault("SORA_OFFLINE", "1")

import json
import uuid
from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, RetrainLog


@pytest.fixture
def configured_db(tmp_path):
    """A database of this test's own, wired in where the code looks for one."""
    engine = create_engine(f"sqlite:///{tmp_path / 'configured.db'}")
    Base.metadata.create_all(bind=engine, tables=[RetrainLog.__table__])
    Session = sessionmaker(bind=engine)
    with patch("app.database.SessionLocal", Session):
        yield Session
    engine.dispose()


def _add_run(Session, *, status="success", metrics=None, when=None, version="v9"):
    with Session() as db:
        db.add(RetrainLog(
            status=status,
            started_at=when or datetime(2026, 8, 9, 12, 0),
            metrics_json=json.dumps(metrics) if metrics is not None else None,
            model_version=version,
        ))
        db.commit()


def _stats():
    from app.mlflow_tracking import get_experiment_stats
    # MLflow is unrelated to what is under test and reaches the network.
    with patch("app.mlflow_tracking._OFFLINE", True):
        return get_experiment_stats()


# --- it reads the configured database --------------------------------------


def test_metrics_from_the_configured_database_are_returned(configured_db):
    """The defect in one assertion: before the fix this returned no metrics."""
    _add_run(configured_db, metrics={"roc_auc": 0.83, "f1_score": 0.71})

    result = _stats()

    assert result.get("roc_auc") == 0.83, (
        f"the configured database holds a successful retrain and its metrics "
        f"were not read: {result}"
    )
    assert result.get("f1_score") == 0.71
    assert result.get("_source") == "retrain_log"


def test_the_old_relative_path_is_not_opened(configured_db, tmp_path, monkeypatch):
    """`sqlite3.connect` creates the file it cannot find, so absence is the test.

    Run from a directory of this test's own, so the property is asserted
    against a path that certainly did not exist a moment ago. An earlier version
    checked the repository's own `data/sora.db` and skipped when a developer
    already had one -- which is the case where the assertion matters least and
    the case where it silently stopped running. Deleting theirs to make it
    testable would be a worse defect than the one being fixed.
    """
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)

    _add_run(configured_db, metrics={"roc_auc": 0.5})
    _stats()

    assert not (tmp_path / "data" / "sora.db").exists(), (
        "reading stats created a database at the old relative path"
    )


def test_a_misleading_repository_database_is_ignored(configured_db, tmp_path, monkeypatch):
    """A file at the old path must not be consulted, even when it is readable.

    Absence alone would also be satisfied by code that opens the path only when
    it already exists, so this puts a plausible answer there and requires the
    configured one to win.
    """
    import sqlite3

    decoy_root = tmp_path / "decoy"
    (decoy_root / "data").mkdir(parents=True)
    con = sqlite3.connect(decoy_root / "data" / "sora.db")
    con.execute("create table retrain_log (metrics_json text, model_version text, "
                "started_at text, status text)")
    con.execute("insert into retrain_log values (?,?,?,?)",
                (json.dumps({"roc_auc": 0.11}), "decoy", "2099-01-01", "success"))
    con.commit(); con.close()

    _add_run(configured_db, metrics={"roc_auc": 0.83})
    monkeypatch.chdir(decoy_root)

    assert _stats().get("roc_auc") == 0.83, "the decoy at the old relative path was read"


def test_the_working_directory_does_not_change_the_answer(configured_db, tmp_path, monkeypatch):
    """A relative path makes the result depend on where the process started."""
    _add_run(configured_db, metrics={"roc_auc": 0.83})

    here = _stats().get("roc_auc")
    monkeypatch.chdir(tmp_path)
    there = _stats().get("roc_auc")

    assert here == there == 0.83, f"the answer moved with the cwd: {here} vs {there}"


def test_the_most_recent_successful_run_is_the_one_reported(configured_db):
    """Ordering is part of the contract, and a wrong database would fail it too."""
    _add_run(configured_db, metrics={"roc_auc": 0.60},
             when=datetime(2026, 8, 1, 12, 0), version="old")
    _add_run(configured_db, metrics={"roc_auc": 0.90},
             when=datetime(2026, 8, 9, 12, 0), version="new")

    result = _stats()

    assert result.get("roc_auc") == 0.90
    assert result.get("model_version") == "new"


# --- the two outcomes that used to look identical ---------------------------


def test_an_empty_table_reports_no_runs_rather_than_an_error(configured_db):
    result = _stats()

    assert result["total_runs"] == 0
    assert result.get("_retrain_log") == "no successful runs"
    assert "roc_auc" not in result


def test_a_failing_query_is_not_reported_as_zero_runs(tmp_path):
    """The distinction the old code could not make.

    A database that cannot be queried is a fact about the system. Reporting it
    as "no successful runs" is what kept this defect invisible in production for
    as long as it lasted.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")  # no tables
    Session = sessionmaker(bind=engine)

    with patch("app.database.SessionLocal", Session):
        result = _stats()

    assert result.get("_retrain_log") == "unavailable", (
        f"a failed query was reported as data: {result}"
    )
    assert "no successful runs" != result.get("_retrain_log")


def test_a_database_failure_does_not_leak_its_message(tmp_path):
    """The old code put `str(e)` in a response served by a public endpoint."""
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    Session = sessionmaker(bind=engine)

    with patch("app.database.SessionLocal", Session):
        result = _stats()

    rendered = json.dumps(result)
    for leak in ("Traceback", "OperationalError", "no such table", "sqlite3"):
        assert leak not in rendered, f"the response carries {leak!r}: {rendered}"


# --- against the database production actually runs --------------------------


def test_it_reads_postgresql_when_that_is_what_is_configured():
    """Everything above builds its own SQLite, and the defect was about not
    using the configured backend at all -- so one case has to exercise the real
    one. Skipped unless the suite is pointed at PostgreSQL; the CI job that does
    is `integration-tests`, which runs against the PostgreSQL service. Named
    here because a reader otherwise cannot tell whether this case ever runs --
    it skips silently everywhere else, and an earlier version of this docstring
    named the wrong job.

    Uses the application's own `SessionLocal` rather than a fixture engine: what
    is under test is that `get_experiment_stats` reads *that*.
    """
    from app.database import RetrainLog, SessionLocal

    if SessionLocal.kw["bind"].url.get_backend_name() != "postgresql":
        pytest.skip("not configured against PostgreSQL")

    marker = f"ci-{uuid.uuid4().hex[:12]}"
    repo_db = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "sora.db")
    existed = os.path.exists(repo_db)

    with SessionLocal() as db:
        db.add(RetrainLog(status="success", started_at=datetime(2099, 1, 1),
                          metrics_json=json.dumps({"roc_auc": 0.77}),
                          model_version=marker))
        db.commit()
    try:
        result = _stats()

        # started_at in 2099 so this row is the newest regardless of what the
        # shared CI database already holds -- otherwise the assertion would
        # depend on the order other tests happened to run in.
        assert result.get("model_version") == marker, (
            f"the configured PostgreSQL row was not read: {result}"
        )
        assert result.get("roc_auc") == 0.77
        assert result.get("_source") == "retrain_log"
        assert os.path.exists(repo_db) == existed, (
            "reading from PostgreSQL touched the repository's SQLite file"
        )
    finally:
        with SessionLocal() as db:
            deleted = (db.query(RetrainLog)
                       .filter(RetrainLog.model_version == marker).delete())
            db.commit()
        # Asserted, not assumed. A `finally` that runs proves the statement was
        # reached, not that the row is gone -- and a row left behind in the
        # shared CI database would be read by the next run as if it were its own.
        assert deleted == 1, f"cleanup removed {deleted} rows, expected 1"
