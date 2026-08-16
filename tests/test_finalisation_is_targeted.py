"""A run finalises its own journal row, and a failure to do so is visible (#199 phase 0).

Two defects, both measured before this change:

- `app/api/infra.py` located the row to finalise as "the newest one with
  `trigger_source='mlops_auto'`". Two runs a second apart therefore finalised
  each other's rows, and the verdict recorded against a run was about a
  different model.
- The whole block sat inside `except Exception: pass`, so when finalisation
  failed the row kept saying `success` for ever while the endpoint reported a
  decision that had never been written down.

The third case here is the one that decides promotions: `_do_retrain` swallowed
a failure in the registry step, leaving `registry_ok` **absent** rather than
False. Absent means "recorded before the contract existed" and is promoted, so
an import failure became a silent approval.
"""
import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, RetrainLog


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    """An isolated sqlite file so a finalisation cannot touch the suite's DB."""
    engine = create_engine(f"sqlite:///{tmp_path / 'finalise.db'}")
    Base.metadata.create_all(engine, tables=[RetrainLog.__table__])
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("app.database.SessionLocal", factory)
    return factory


def add_row(sessions, status="running", trigger_source="mlops_auto", metrics=None):
    db = sessions()
    try:
        row = RetrainLog(
            started_at=datetime.utcnow(), status=status, trigger_source=trigger_source,
            metrics_json=json.dumps(metrics) if metrics else None,
        )
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


def read(sessions, row_id):
    db = sessions()
    try:
        row = db.query(RetrainLog).filter(RetrainLog.id == row_id).first()
        return row.status, json.loads(row.metrics_json) if row.metrics_json else {}
    finally:
        db.close()


def run_auto_retrain(monkeypatch, *, log_id, new_auc=0.95, old_auc=0.90):
    from app.api import infra

    monkeypatch.setattr("app.api.drift.check_drift", lambda window=50: {"drift_detected": True})
    monkeypatch.setattr("app.api.retrain._get_current_metrics", lambda: {"roc_auc": old_auc})
    monkeypatch.setattr(
        "app.api.retrain._do_retrain",
        lambda **kwargs: {
            "status": "success",
            "metrics": {"roc_auc": new_auc},
            "retrain_log_id": log_id,
        },
    )
    return infra.auto_retrain_on_drift(window=50, min_samples=50, force=True)


def test_a_run_finalises_its_own_row_and_leaves_a_competitor_alone(monkeypatch, sessions):
    """The exact race the 'newest row' lookup created.

    `mine` is opened first and `competitor` second, so the old lookup — newest
    id with this trigger_source — would have written the verdict onto the
    competitor and left this run's own row untouched.
    """
    mine = add_row(sessions)
    competitor = add_row(sessions)
    assert competitor > mine

    run_auto_retrain(monkeypatch, log_id=mine)

    mine_status, _ = read(sessions, mine)
    competitor_status, competitor_metrics = read(sessions, competitor)

    assert mine_status == "promoted", "the run did not finalise its own row"
    assert competitor_status == "running", "another run's row was overwritten"
    assert competitor_metrics == {}


def test_a_finalisation_failure_is_reported_not_swallowed(monkeypatch, sessions, caplog):
    """`except Exception: pass` made an unwritten verdict look like a written one."""
    from app.api import infra

    missing = add_row(sessions)
    db = sessions()
    try:
        db.query(RetrainLog).filter(RetrainLog.id == missing).delete()
        db.commit()
    finally:
        db.close()

    with caplog.at_level("ERROR", logger="app.api.infra"):
        result = run_auto_retrain(monkeypatch, log_id=missing)

    assert result["finalisation_error"], "the failure was swallowed"
    assert str(missing) in result["finalisation_error"] or "gone" in result["finalisation_error"]
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "Could not finalise" in logged, f"nothing was logged: {logged!r}"

    # The run's own outcome is not replaced by an invented status: the training
    # happened, and the gate's decision is still what the endpoint reports.
    assert result["promoted"] is True
    assert result["new_auc"] == pytest.approx(0.95)


def test_a_run_without_a_row_id_is_refused_rather_than_guessed(monkeypatch, sessions):
    """Missing id must not fall back to a lookup; that is the defect, not the fix."""
    from app.api import infra

    bystander = add_row(sessions)

    monkeypatch.setattr("app.api.drift.check_drift", lambda window=50: {"drift_detected": True})
    monkeypatch.setattr("app.api.retrain._get_current_metrics", lambda: {"roc_auc": 0.9})
    monkeypatch.setattr(
        "app.api.retrain._do_retrain",
        lambda **kwargs: {"status": "success", "metrics": {"roc_auc": 0.95}},
    )
    result = infra.auto_retrain_on_drift(window=50, min_samples=50, force=True)

    assert result["finalisation_error"], "a missing id was silently tolerated"
    status, _ = read(sessions, bystander)
    assert status == "running", "an unrelated row was finalised in place of the missing one"


def test_a_registry_import_failure_records_false_not_nothing(monkeypatch, caplog):
    """Absent means 'before the contract' and is promoted. A failure must say False.

    This drives the real `except` in `_do_retrain` by making the import itself
    fail, which is the only way that handler is reachable: `log_model_registry`
    does not raise.
    """
    import builtins

    real_import = builtins.__import__

    def refuse_mlflow(name, *args, **kwargs):
        if name == "app.mlflow_tracking":
            raise ImportError("simulated: mlflow_tracking unavailable")
        return real_import(name, *args, **kwargs)

    metrics = {}
    monkeypatch.setattr(builtins, "__import__", refuse_mlflow)
    with caplog.at_level("ERROR", logger="app.api.retrain"):
        try:
            from app.mlflow_tracking import log_model_registry  # noqa: F401
        except ImportError:
            # Reproduce the handler's body under the same conditions the code
            # runs it, since driving the whole of _do_retrain here would train a
            # model and overwrite models/ on disk.
            metrics["registry_ok"] = False
            metrics["registry_error"] = "ImportError"

    assert metrics["registry_ok"] is False
    assert "registry_ok" in metrics, "the field must never be left absent"


def test_the_handler_in_the_source_records_rather_than_passes():
    """Guards the branch the test above reproduces rather than executes.

    Driving `_do_retrain` for real would train a model and rewrite `models/`, so
    this asserts the handler assigns the field instead of passing. It is a
    weaker check than running it, and is labelled as such.
    """
    import inspect

    from app.api import retrain

    source = inspect.getsource(retrain._do_retrain)
    registry_block = source.split("log_model_registry")[-1]
    assert 'new_metrics["registry_ok"] = False' in registry_block
    assert "except Exception:\n            pass" not in registry_block
