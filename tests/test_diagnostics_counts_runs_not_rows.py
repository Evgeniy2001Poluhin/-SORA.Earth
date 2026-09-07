"""Every reader of `retrain_log` counts retrains, not the rows they wrote (#199).

Contract point 10 -- "metrics and UI count one run once" -- was implemented as
`count_physical_runs` and wired into `/admin/ai-control`. **Three** other
readers kept counting rows, and the first draft of this file fixed one of them
and called it "the second reader" -- the same sampling mistake the project
keeps making, made inside a change about that mistake. The list came from
`grep -rn "query(RetrainLog)" app/`, not from memory:

    app/api/admin_diagnostics.py:30-33   total/success/failed/recent
    app/api/admin_snapshot.py:120,127-8  total/success/failed
    app/scheduler.py:525                 retrain_history_count

`/admin/diagnostics` counted like this:

    retrain_total   = db.query(RetrainLog).count()
    retrain_success = ...filter(status == "success").count()

One closed-loop cycle writes two rows -- the training and the decision -- and
`retrain_models` writes two both saying `success`. So this endpoint reported
one run as two, which is the figure the issue was opened about.

`tests/test_admin_timeline_diagnostics.py` covers the endpoint and did not
catch it: it asserts the response has the keys, never what is in them. A test
that watches the shape cannot tell a doubled count from a correct one.

These tests use an isolated SQLite session and call the endpoint function
directly, so the numbers are exact rather than relative to whatever else the
shared fixture database happens to hold.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.admin_diagnostics import admin_diagnostics
from app.api.admin_snapshot import get_admin_snapshot
from app.database import Base, RetrainLog, count_physical_runs


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _cycle(db, run_id, *, started, training="success", decision="rejected"):
    """The two rows one closed-loop cycle writes, as it really writes them."""
    db.add(RetrainLog(run_id=run_id, job_name="model_retrain",
                      status=training, trigger_source="scheduler", started_at=started))
    db.add(RetrainLog(run_id=run_id, job_name="closed_loop",
                      status=decision, trigger_source="scheduler", started_at=started))
    db.commit()


def _run_of_two_successes(db, run_id, *, started):
    """The `retrain_models` shape: two rows, one run, both saying `success`.

    This is the shape where a row count and a run count disagree about
    `success` -- the closed-loop shape has one `success` row per cycle, so
    both answers are the same there and an assertion over it proves nothing.
    """
    for job in ("model_retrain", "model_retrain"):
        db.add(RetrainLog(run_id=run_id, job_name=job, status="success",
                          trigger_source="scheduler", started_at=started))
    db.commit()


def test_one_cycle_is_one_run(db):
    """Two rows, one physical retrain."""
    _cycle(db, "run-a", started=datetime.utcnow())

    body = admin_diagnostics(hours=720, db=db, _admin=object())

    assert db.query(RetrainLog).count() == 2, "the fixture must write two rows"
    assert body["retrain"]["total"] == 1, (
        "one cycle counted as more than one run; the endpoint is counting rows"
    )


def test_a_status_is_counted_once_per_run(db):
    """`success` belongs to the run, not to each row that carries it.

    `retrain_models` writes two rows both saying `success` for one run. Under a
    row count that is two successful retrains, and there was one.
    """
    db.add(RetrainLog(run_id="run-b", job_name="model_retrain", status="success",
                      started_at=datetime.utcnow()))
    db.add(RetrainLog(run_id="run-b", job_name="model_retrain", status="success",
                      started_at=datetime.utcnow()))
    db.commit()

    body = admin_diagnostics(hours=720, db=db, _admin=object())

    assert body["retrain"]["success"] == 1
    assert body["retrain"]["total"] == 1


def test_two_cycles_do_not_collapse_into_one(db):
    """The negative control, and the one that matters.

    Grouping is only correct if it separates runs that look alike. These two
    share a trigger source and are seconds apart -- exactly what the old code
    had to work with, and exactly what is not enough.
    """
    now = datetime.utcnow()
    _cycle(db, "run-c", started=now)
    _cycle(db, "run-d", started=now + timedelta(seconds=1))

    body = admin_diagnostics(hours=720, db=db, _admin=object())

    assert body["retrain"]["total"] == 2
    assert db.query(RetrainLog).count() == 4


def test_rows_without_a_run_id_are_counted_individually(db):
    """History predates the column and must not be reinterpreted.

    Guessing a grouping for rows that never recorded one would put a
    reconstruction where a fact is expected, and would move historical figures.
    """
    db.add(RetrainLog(run_id=None, job_name="model_retrain", status="success",
                      started_at=datetime.utcnow()))
    db.add(RetrainLog(run_id=None, job_name="model_retrain", status="success",
                      started_at=datetime.utcnow()))
    db.commit()

    body = admin_diagnostics(hours=720, db=db, _admin=object())

    assert body["retrain"]["total"] == 2


def test_recent_counts_runs_in_the_window_not_rows(db):
    """`recent` was the fourth row count and is the same defect.

    The window filter has to be applied before the grouping, or a cycle that
    started inside it is counted twice there too.
    """
    now = datetime.utcnow()
    _cycle(db, "run-old", started=now - timedelta(hours=48))
    _cycle(db, "run-new", started=now - timedelta(minutes=5))

    body = admin_diagnostics(hours=24, db=db, _admin=object())

    assert body["retrain"]["recent"] == 1, "the window must hold one run, not two rows"
    assert body["retrain"]["total"] == 2, "and both runs overall"


def test_the_helper_and_the_endpoint_do_not_diverge(db):
    """One definition of "how many runs", used by both readers.

    `/admin/ai-control` already calls `count_physical_runs`. Two endpoints
    answering the same question differently is how this project acquired two
    promotion gates.
    """
    _cycle(db, "run-e", started=datetime.utcnow())
    _cycle(db, "run-f", started=datetime.utcnow())

    body = admin_diagnostics(hours=720, db=db, _admin=object())

    assert body["retrain"]["total"] == count_physical_runs(db)
    assert body["retrain"]["success"] == count_physical_runs(db, status="success")
    assert body["retrain"]["failed"] == count_physical_runs(db, status="failed")


def test_the_snapshot_endpoint_counts_runs_too(db):
    """The reader the first draft of this file missed.

    `/admin/snapshot` counted rows the same way, and would have kept doing so
    while `/admin/diagnostics` was fixed beside it -- two admin pages showing
    different totals for the same thing.

    The rows are the `retrain_models` shape -- **both** saying `success` for one
    physical run -- and not the closed-loop shape. With one `success` row per
    cycle, a row count and a run count give the same answer, and the first
    version of this test passed with `success_count` reverted to a row count:
    the fixture was supplying the very agreement it was meant to test.
    """
    _run_of_two_successes(db, "run-g", started=datetime.utcnow())
    _run_of_two_successes(db, "run-h", started=datetime.utcnow())

    snapshot = get_admin_snapshot(db=db, _admin=object())

    assert db.query(RetrainLog).count() == 4, "the fixture must write four rows"
    assert db.query(RetrainLog).filter(RetrainLog.status == "success").count() == 4, (
        "and all four must say success, or a row count and a run count agree "
        "and this test cannot fail"
    )
    assert snapshot.retrain_log_summary.total == 2
    assert snapshot.retrain_log_summary.success_count == 2


def test_the_two_admin_surfaces_agree(db):
    """They answer the same question and must not diverge.

    Two endpoints reporting different totals for one table is how the project
    acquired two promotion gates and two metrics endpoints.
    """
    _cycle(db, "run-i", started=datetime.utcnow())

    diagnostics = admin_diagnostics(hours=720, db=db, _admin=object())
    snapshot = get_admin_snapshot(db=db, _admin=object())

    assert diagnostics["retrain"]["total"] == snapshot.retrain_log_summary.total


def test_the_scheduler_status_counts_runs_too(tmp_path, monkeypatch):
    """The third reader, and the one nothing was watching.

    `get_scheduler_status` decorates the payload the scheduler container
    publishes with `retrain_history_count`, read straight from the table. It is
    the closed loop's own figure about the closed loop's own work, and the loop
    writes two rows per cycle -- so it reported itself at double.

    Driven through a real session against a file database, because the function
    opens its own `SessionLocal`: an in-memory SQLite gives each connection its
    own empty schema, and the rows written here would not be there to count.
    """
    import json

    from sqlalchemy import create_engine as _create_engine
    from sqlalchemy.orm import sessionmaker as _sessionmaker

    import app.database as database
    from app import scheduler as scheduler_module

    engine = _create_engine(f"sqlite:///{tmp_path/'sched.db'}")
    Base.metadata.create_all(engine)
    Session = _sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", Session)

    seed = Session()
    _run_of_two_successes(seed, "run-j", started=datetime.utcnow())
    _run_of_two_successes(seed, "run-k", started=datetime.utcnow())
    assert seed.query(RetrainLog).count() == 4
    seed.close()

    class _Redis:
        @staticmethod
        def get(_key):
            return json.dumps({"running": True, "jobs": []})

    monkeypatch.setattr("app.redis_cache.REDIS_AVAILABLE", True)
    monkeypatch.setattr("app.redis_cache.redis_client", _Redis())

    status = scheduler_module.get_scheduler_status()

    assert status["source"] == "scheduler_container", (
        "the Redis branch must be the one under test; the fallback does not "
        "carry this field and the assertion below would pass vacuously"
    )
    assert status["retrain_history_count"] == 2, (
        "four rows, two physical runs; the scheduler is counting rows"
    )
