"""`status` was answering three questions at once (#199 phase 2B).

Measured on a real cycle 2026-08-16: a row read `success` while its own
`metrics_json` held `registry_ok=False`. The training had succeeded and the
registration had not, and one field could not say both.

The split adds `training_status`, `registry_status`, `promotion_status` and
`failure_reason`. `status` stays, derived by `project_status` -- one definition,
because two implementations of "what does this mean now" is how the closed loop
and `app/api/infra.py` came to have two different promotion gates.

What these tests are for is the compatibility half. Twelve readers still filter
on `status`, so the projection must reproduce exactly the values written before,
and a writer that has not moved yet must keep working untouched.
"""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, RetrainLog, project_status
from app.scheduler import _finish_retrain_log, _start_retrain_log


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'split.db'}")
    Base.metadata.create_all(engine, tables=[RetrainLog.__table__])
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("app.database.SessionLocal", factory)
    return factory


def read(sessions, log_id):
    db = sessions()
    try:
        return db.query(RetrainLog).filter(RetrainLog.id == log_id).first()
    finally:
        db.close()


@pytest.mark.parametrize("stages,expected", [
    (dict(training_status="success"), "success"),
    (dict(training_status="failed"), "failed"),
    (dict(training_status="skipped"), "skipped"),
    (dict(promotion_status="promoted"), "promoted"),
    (dict(promotion_status="rejected"), "rejected"),
    # The gate has ruled, so its verdict is the run's status even though the
    # training succeeded -- which is what the old single field already did.
    (dict(training_status="success", promotion_status="rejected"), "rejected"),
    (dict(training_status="success", promotion_status="promoted"), "promoted"),
    (dict(), "running"),
])
def test_the_projection_reproduces_the_values_readers_already_expect(stages, expected):
    """Twelve readers filter on these strings. None may change meaning."""
    assert project_status(**stages) == expected


def test_a_failed_registration_does_not_silently_change_the_top_level_status():
    """Deliberately out of scope here, and asserted so it stays deliberate.

    Making a failed registration visible in `status` is the #189/#198 contract
    change. Doing it in the same commit as the split would make it impossible to
    tell which change moved a figure.
    """
    assert project_status(training_status="success", registry_status="failed") == "success"


def test_one_row_can_now_say_the_training_worked_and_the_registration_did_not(sessions):
    """The case the single field could not express."""
    log_id = _start_retrain_log(trigger_source="t", job_name="model_retrain")
    _finish_retrain_log(
        log_id=log_id, training_status="success", registry_status="failed",
        message="done", metrics={"roc_auc": 0.9},
    )

    row = read(sessions, log_id)
    assert row.training_status == "success"
    assert row.registry_status == "failed"
    assert row.status == "success", "the compatibility projection changed meaning"


def test_the_gates_verdict_and_its_reason_land_in_their_own_fields(sessions):
    log_id = _start_retrain_log(trigger_source="t", job_name="closed_loop")
    _finish_retrain_log(
        log_id=log_id, promotion_status="rejected",
        failure_reason="AUC below minimum threshold", message="Closed loop: rejected",
    )

    row = read(sessions, log_id)
    assert row.promotion_status == "rejected"
    assert row.failure_reason == "AUC below minimum threshold"
    assert row.status == "rejected"


def test_a_writer_that_has_not_moved_yet_keeps_working_unchanged(sessions):
    """Dual-write means the un-migrated callers are untouched, not broken."""
    log_id = _start_retrain_log(trigger_source="t", job_name="model_retrain")
    _finish_retrain_log(log_id=log_id, status="skipped", message="lock held")

    row = read(sessions, log_id)
    assert row.status == "skipped"
    assert row.training_status is None, "a stage was invented for a caller that said nothing"
    assert row.promotion_status is None


def test_finishing_a_run_that_says_nothing_at_all_is_refused(sessions):
    """A row claiming to be finished while reporting no outcome is not a record.

    Making `status` optional opened this hole; the alternative -- defaulting to
    something -- would write a verdict nobody chose.
    """
    log_id = _start_retrain_log(trigger_source="t", job_name="model_retrain")

    with pytest.raises(ValueError, match="requires either a status or at least one stage"):
        _finish_retrain_log(log_id=log_id, message="finished, somehow")


def test_status_cannot_disagree_with_the_stages_it_is_derived_from(sessions):
    """The point of deriving rather than passing both.

    Before this, a caller could write `status="success"` beside a rejection and
    nothing would notice. Now the caller supplies stages and the projection is
    the only writer of `status`.
    """
    log_id = _start_retrain_log(trigger_source="t", job_name="closed_loop")
    _finish_retrain_log(log_id=log_id, training_status="success",
                        promotion_status="rejected", failure_reason="registry")

    row = read(sessions, log_id)
    assert row.status == project_status(row.training_status, row.registry_status,
                                        row.promotion_status)
    assert row.status == "rejected"


def test_rows_written_before_the_columns_existed_keep_their_status(sessions):
    """NULL stages on an old row must not make it read as `running`."""
    db = sessions()
    try:
        row = RetrainLog(started_at=datetime.utcnow(), status="success",
                         trigger_source="legacy", job_name="model_retrain")
        db.add(row)
        db.commit()
        log_id = row.id
    finally:
        db.close()

    stored = read(sessions, log_id)
    assert stored.status == "success"
    assert stored.training_status is None
    # The projection is not applied on read; `status` is a stored column and
    # stays what it was. Applying it on read would rewrite history.
    assert project_status(stored.training_status) == "running"
    assert stored.status != project_status(stored.training_status)
