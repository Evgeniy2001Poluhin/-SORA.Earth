"""One physical retrain is one run, however many rows it writes (#199 phase 2A).

Measured on a real closed-loop cycle 2026-08-16, before this change:

    id=1 job='model_retrain'  status='success'   registry_ok=False
    id=2 job='closed_loop'    status='rejected'  model_version=None

Two rows, related by nothing but a timestamp. `retrain_models` produced two rows
both saying `success`, so `admin_ai` counted one run twice.

The negative case is the one that matters: two cycles overlapping in time must
stay separable. Proximity in time and a shared `trigger_source` are exactly what
the old code had to work with, and exactly what is not enough.
"""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, RetrainLog, count_physical_runs
from app.scheduler import _start_retrain_log, new_run_id

def _drift(detected: bool):
    """A drift result shaped like the one `compute_drift` really returns.

    These tests used to patch `app.api.drift.check_drift` with a plain dict.
    That mock agreed with the caller while the real function stopped agreeing:
    the contract migration changed the return to a Pydantic model, and
    `drift_result.get(...)` would have raised AttributeError in production
    without a single test going red. The mock now has the shape the real
    function produces, so the next such change fails here.
    """
    from app.schemas import ModelDriftMeasured

    return ModelDriftMeasured(
        status="ok", drift_detected=detected, window=50,
        observations=100, features={}, reason_code=None,
    )


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'runs.db'}")
    Base.metadata.create_all(engine, tables=[RetrainLog.__table__])
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("app.database.SessionLocal", factory)
    return factory


def add(sessions, *, status, run_id, job_name="model_retrain",
        trigger_source="scheduler", started_at=None):
    db = sessions()
    try:
        row = RetrainLog(
            started_at=started_at or datetime.utcnow(), status=status,
            job_name=job_name, trigger_source=trigger_source, run_id=run_id,
        )
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


def rows(sessions):
    db = sessions()
    try:
        return [(r.id, r.job_name, r.status, r.run_id)
                for r in db.query(RetrainLog).order_by(RetrainLog.id).all()]
    finally:
        db.close()


def test_start_retrain_log_stores_a_run_even_when_none_is_supplied(sessions):
    """A caller that writes only one row still gets a run of its own."""
    log_id = _start_retrain_log(trigger_source="test", job_name="model_retrain")

    db = sessions()
    try:
        assert db.query(RetrainLog).filter(RetrainLog.id == log_id).first().run_id
    finally:
        db.close()


def test_the_supplied_run_is_the_one_stored(sessions):
    run_id = new_run_id()
    log_id = _start_retrain_log(trigger_source="test", job_name="model_retrain",
                                run_id=run_id)

    db = sessions()
    try:
        stored = db.query(RetrainLog).filter(RetrainLog.id == log_id).first()
        assert stored.run_id == run_id
    finally:
        db.close()


def test_a_caller_that_already_has_a_run_does_not_get_a_second(sessions):
    """The whole mechanism: the decision row joins the training run."""
    existing = new_run_id()
    _start_retrain_log(trigger_source="t", job_name="model_retrain", run_id=existing)
    _start_retrain_log(trigger_source="t", job_name="closed_loop", run_id=existing)

    db = sessions()
    try:
        stored = {r.run_id for r in db.query(RetrainLog).all()}
    finally:
        db.close()
    assert stored == {existing}, "a second run id was minted for the same cycle"
    assert count_physical_runs(sessions()) == 1, "one cycle counted as more than one run"


def test_two_rows_of_one_cycle_count_once_not_twice(sessions):
    """`retrain_models` writes two rows both saying success. That was two runs."""
    run = new_run_id()
    add(sessions, status="success", run_id=run, job_name="model_retrain")
    add(sessions, status="success", run_id=run, job_name="retrain_job")

    db = sessions()
    assert count_physical_runs(db) == 1
    assert count_physical_runs(db, status="success") == 1
    assert db.query(RetrainLog).count() == 2, "the rows themselves are still both there"


def test_overlapping_cycles_stay_separable_when_time_and_trigger_cannot(sessions):
    """The negative case. Interleaved in time, identical trigger_source.

    This is precisely the situation the old code could not resolve: it grouped
    by 'newest row with this trigger_source', so two runs a second apart were
    indistinguishable. Ordering the writes so the two cycles interleave means a
    time-based grouping would get it wrong.
    """
    first, second = new_run_id(), new_run_id()
    base = datetime(2026, 8, 16, 12, 0, 0)

    add(sessions, status="success", run_id=first, started_at=base,
        trigger_source="scheduler", job_name="model_retrain")
    add(sessions, status="success", run_id=second, started_at=base + timedelta(seconds=1),
        trigger_source="scheduler", job_name="model_retrain")
    add(sessions, status="rejected", run_id=first, started_at=base + timedelta(seconds=2),
        trigger_source="scheduler", job_name="closed_loop")
    add(sessions, status="promoted", run_id=second, started_at=base + timedelta(seconds=3),
        trigger_source="scheduler", job_name="closed_loop")

    db = sessions()
    assert count_physical_runs(db) == 2, "two cycles must not collapse into one"
    assert count_physical_runs(db, status="rejected") == 1
    assert count_physical_runs(db, status="promoted") == 1

    # And the decision of each cycle is recoverable, which is the point.
    by_run = {}
    for _, job, status, run in rows(sessions):
        by_run.setdefault(run, {})[job] = status
    assert by_run[first]["closed_loop"] == "rejected"
    assert by_run[second]["closed_loop"] == "promoted"


def test_rows_written_before_the_column_are_counted_as_they_always_were(sessions):
    """NULL means 'this predates the column', not 'this belongs to no run'.

    Backfilling a guessed grouping would put a reconstruction where a fact is
    expected, and no reader could tell them apart afterwards. Counting each old
    row as its own run is exactly what the old readers did, so historical
    figures do not move.
    """
    add(sessions, status="success", run_id=None)
    add(sessions, status="success", run_id=None)
    add(sessions, status="failed", run_id=None)

    db = sessions()
    assert count_physical_runs(db) == 3
    assert count_physical_runs(db, status="success") == 2
    assert count_physical_runs(db, status="failed") == 1


def test_old_and_new_rows_are_counted_together_without_either_distorting_the_other(sessions):
    legacy = [add(sessions, status="success", run_id=None) for _ in range(2)]
    run = new_run_id()
    add(sessions, status="success", run_id=run, job_name="model_retrain")
    add(sessions, status="success", run_id=run, job_name="closed_loop")

    db = sessions()
    assert len(legacy) == 2
    # Two legacy rows = two runs, plus one grouped cycle = three.
    assert count_physical_runs(db, status="success") == 3
    assert db.query(RetrainLog).count() == 4


def test_a_run_whose_rows_disagree_counts_under_each_status(sessions):
    """The normal shape: training succeeded, the decision refused promotion.

    Counting it once under `success` and once under `rejected` is the honest
    answer -- the run did both -- and collapsing it to one would require picking
    which half to discard.
    """
    run = new_run_id()
    add(sessions, status="success", run_id=run, job_name="model_retrain")
    add(sessions, status="rejected", run_id=run, job_name="closed_loop")

    db = sessions()
    assert count_physical_runs(db) == 1
    assert count_physical_runs(db, status="success") == 1
    assert count_physical_runs(db, status="rejected") == 1


def test_the_closed_loop_gives_its_decision_the_training_run_id(monkeypatch, sessions):
    """End to end over the seam that phase 2A exists to create."""
    from app import scheduler as sched

    class _Lock:
        def acquire(self):
            return True

        def release(self):
            pass

    training_run = new_run_id()

    def fake_retrain(**kwargs):
        # What the real _do_retrain does: open its own row under this run.
        _start_retrain_log(trigger_source="probe", job_name="model_retrain",
                           run_id=training_run)
        return {
            "status": "success",
            "metrics": {"roc_auc": 0.9063, "test_positive": 800, "test_negative": 2600},
            "run_id": training_run,
        }

    monkeypatch.setattr("app.locks.RedisLock", lambda **kwargs: _Lock())
    monkeypatch.setattr("app.api.drift.compute_drift", lambda window=50: _drift(True))
    monkeypatch.setattr("app.api.retrain._get_current_metrics", lambda: {"roc_auc": 0.85})
    monkeypatch.setattr("app.api.retrain._do_retrain", fake_retrain)

    sched.closed_loop_retrain(trigger_source="probe")

    written = rows(sessions)
    assert len(written) == 2, f"expected the training row and the decision row: {written}"
    assert {r[3] for r in written} == {training_run}, "the decision minted its own run"
    assert count_physical_runs(sessions()) == 1


def test_do_retrain_puts_the_run_it_opened_into_its_result():
    """The seam the end-to-end test above cannot reach.

    That test replaces `_do_retrain` with a fake that returns the id itself, so
    it verifies the loop's half and nothing about the real function. Mutation
    testing caught it: setting `"run_id": None` in `_do_retrain` left the suite
    green.

    Running the real `_do_retrain` here would train a model and rewrite
    `models/`, which this repository forbids from a test tree, so the contract
    is checked in the source. That is weaker than executing it and is labelled
    as such -- it would not catch the value being wrong, only the key being
    dropped. Executing it properly needs the staged-artefact work in phase 3.
    """
    import inspect

    from app.api import retrain

    source = inspect.getsource(retrain._do_retrain)
    assert "run_id = new_run_id()" in source, "the run is not minted"
    assert 'run_id=run_id' in source, "the row is opened without the run"
    assert '"run_id": run_id,' in source, (
        "the result does not carry the run, so the caller's decision row cannot join it"
    )
