"""Re-registering an existing model must be safe, idempotent, and must refuse when it cannot be (#189).

The interesting cases are the refusals. A retry that always registered
*something* would look successful every time while quietly filing whichever
model happens to be on disk under the identity of the run being retried.
"""
import json
import pickle

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import mlflow_tracking, registry_retry
from app.database import Base, RetrainLog


VERSION = "20260816_101500"


@pytest.fixture
def sessions(tmp_path):
    """An isolated sqlite file, so a retry that writes cannot touch the suite's DB."""
    engine = create_engine(f"sqlite:///{tmp_path / 'retry.db'}")
    Base.metadata.create_all(engine, tables=[RetrainLog.__table__])
    return sessionmaker(bind=engine)


@pytest.fixture
def models(tmp_path):
    """A models directory holding one artefact and the meta that names its run."""
    directory = tmp_path / "models"
    directory.mkdir()
    (directory / "model.pkl").write_bytes(pickle.dumps({"fake": "model"}))
    (directory / "meta.json").write_text(json.dumps({"retrained_at": VERSION}))
    return directory


def make_run(sessions, *, model_version=VERSION, registry_ok=False):
    from datetime import datetime

    db = sessions()
    try:
        metrics = {"roc_auc": 0.9063, "test_samples": 3400, "split_kind": "stratified_by_outcome"}
        if registry_ok is not None:
            metrics["registry_ok"] = registry_ok
        row = RetrainLog(
            started_at=datetime.utcnow(), status="success", model_version=model_version,
            metrics_json=json.dumps(metrics),
        )
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


def journal(sessions, run_id):
    db = sessions()
    try:
        row = db.query(RetrainLog).filter(RetrainLog.id == run_id).first()
        return json.loads(row.metrics_json)
    finally:
        db.close()


class Accepting:
    """An MLflow stand-in that accepts the upload."""

    class _Run:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    def __init__(self):
        self.registered = []

    def start_run(self, run_name=None):
        return self._Run()

    def log_metrics(self, metrics):
        self.metrics = metrics

    def set_tag(self, key, value):
        pass

    @property
    def sklearn(self):
        recorder = self

        class _Sklearn:
            def log_model(self, model, name):
                recorder.registered.append(name)

        return _Sklearn()


class Tripwire:
    """Any use is a failure: used where the retry must not reach MLflow at all."""

    def __getattr__(self, name):
        raise AssertionError(f"MLflow was reached when it should not have been: .{name}")


def test_a_matching_artefact_is_registered_without_retraining(monkeypatch, sessions, models):
    monkeypatch.setattr(mlflow_tracking, "_OFFLINE", False)
    run_id = make_run(sessions)
    api = Accepting()

    result = registry_retry.retry_registration(
        run_id, models_dir=str(models), api=api, session_factory=sessions
    )

    assert result["outcome"] == registry_retry.REGISTERED
    assert api.registered == ["RandomForest_retrain"]
    assert journal(sessions, run_id)["registry_ok"] is True
    # Only real numbers reach log_metrics: registry_ok is a bool and split_kind
    # is a string, and MLflow takes neither as a metric.
    assert api.metrics == {"roc_auc": 0.9063, "test_samples": 3400}


def test_a_second_call_after_success_does_not_register_again(monkeypatch, sessions, models):
    """Idempotent through the journal: this is what stops a duplicate version."""
    monkeypatch.setattr(mlflow_tracking, "_OFFLINE", False)
    run_id = make_run(sessions)
    registry_retry.retry_registration(
        run_id, models_dir=str(models), api=Accepting(), session_factory=sessions
    )

    again = registry_retry.retry_registration(
        run_id, models_dir=str(models), api=Tripwire(), session_factory=sessions
    )

    assert again["outcome"] == registry_retry.ALREADY_REGISTERED
    assert journal(sessions, run_id)["registry_ok"] is True


def test_a_superseded_artefact_is_refused_rather_than_registered(monkeypatch, sessions, models):
    """The model this run produced is gone; registering the newer one would be a false record.

    This is the case that decides whether the retry is safe at all. The
    artefacts live under fixed filenames, so the next retrain overwrites them --
    and a retry that took whatever was on disk would file a different model
    under this run's version.
    """
    monkeypatch.setattr(mlflow_tracking, "_OFFLINE", False)
    run_id = make_run(sessions)
    (models / "meta.json").write_text(json.dumps({"retrained_at": "20260816_235959"}))

    result = registry_retry.retry_registration(
        run_id, models_dir=str(models), api=Tripwire(), session_factory=sessions
    )

    assert result["outcome"] == registry_retry.ARTIFACT_SUPERSEDED
    assert result["on_disk_version"] == "20260816_235959"
    assert result["model_version"] == VERSION
    assert journal(sessions, run_id)["registry_ok"] is False, "the journal must not be rewritten"


def test_a_refused_upload_leaves_the_journal_saying_it_is_not_registered(
    monkeypatch, sessions, models
):
    monkeypatch.setattr(mlflow_tracking, "_OFFLINE", False)
    run_id = make_run(sessions)

    class Refusing(Accepting):
        @property
        def sklearn(self):
            class _Sklearn:
                def log_model(self, model, name):
                    raise RuntimeError("PermissionError: '/mlflow'")

            return _Sklearn()

    result = registry_retry.retry_registration(
        run_id, models_dir=str(models), api=Refusing(), session_factory=sessions
    )

    assert result["outcome"] == registry_retry.REGISTRATION_FAILED
    assert journal(sessions, run_id)["registry_ok"] is False


def test_a_row_without_a_model_version_is_refused(monkeypatch, sessions, models):
    """The closed loop's own journal row carries no model_version; only the training row does."""
    monkeypatch.setattr(mlflow_tracking, "_OFFLINE", False)
    run_id = make_run(sessions, model_version=None)

    result = registry_retry.retry_registration(
        run_id, models_dir=str(models), api=Tripwire(), session_factory=sessions
    )

    assert result["outcome"] == registry_retry.NO_MODEL_VERSION


def test_offline_is_reported_as_a_failed_retry_not_as_success(monkeypatch, sessions, models):
    """Offline still means not registered, and the journal must keep saying so."""
    monkeypatch.setattr(mlflow_tracking, "_OFFLINE", True)
    run_id = make_run(sessions)

    result = registry_retry.retry_registration(
        run_id, models_dir=str(models), api=Accepting(), session_factory=sessions
    )

    assert result["outcome"] == registry_retry.REGISTRATION_FAILED
    assert journal(sessions, run_id)["registry_ok"] is False
