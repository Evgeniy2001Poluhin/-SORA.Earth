"""Re-registering an already-trained model reads the run's staged candidate (#327, #189).

A run whose training succeeded and whose MLflow registration failed for a
transient reason should not have to be retrained -- its model is still on disk.
Since #199 phase 4 that model is `runtime/staged/<run_id>/model.pkl`, not
`models/`, which is the immutable seed. This keys the retry on the run id and
reads there.

The interesting cases are the refusals. A retry that always registered
*something* would look successful every time while filing whichever model
happens to be on disk under the run being retried. So: the staged candidate is
gone (pruned), it is still being written, or MLflow refuses again -- each is a
distinct, honest refusal that leaves the journal saying "not registered".
"""
import json
import os
import pickle
import uuid

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
def runtime(tmp_path, monkeypatch):
    """Point the seed/staged/active layout at a throwaway runtime."""
    monkeypatch.setenv("SORA_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("SORA_SEED_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("SORA_MODELS_DIR", str(tmp_path / "models"))
    (tmp_path / "models").mkdir()
    return tmp_path / "runtime"


def _stage(run_id, *, complete=True, incomplete=False):
    """Write a staged candidate for `run_id` the way _do_retrain would."""
    from app.paths import staged_dir

    path = staged_dir(run_id)
    os.makedirs(path, exist_ok=True)
    if complete:
        with open(os.path.join(path, "model.pkl"), "wb") as handle:
            pickle.dump({"fake": "model"}, handle)
    if incomplete:
        from app.model_source import INCOMPLETE_MARKER
        open(os.path.join(path, INCOMPLETE_MARKER), "w").close()
    return path


_UNSET = object()


def make_run(sessions, *, model_version=VERSION, run_id=_UNSET, registry_ok=False):
    from datetime import datetime

    run_id = str(uuid.uuid4()) if run_id is _UNSET else run_id
    db = sessions()
    try:
        metrics = {"roc_auc": 0.9063, "test_samples": 3400, "split_kind": "stratified_by_outcome"}
        if registry_ok is not None:
            metrics["registry_ok"] = registry_ok
        row = RetrainLog(
            started_at=datetime.utcnow(), status="success", model_version=model_version,
            run_id=run_id, metrics_json=json.dumps(metrics),
        )
        db.add(row)
        db.commit()
        return row.id, run_id
    finally:
        db.close()


def journal(sessions, log_id):
    db = sessions()
    try:
        row = db.query(RetrainLog).filter(RetrainLog.id == log_id).first()
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


def test_the_staged_candidate_is_registered_without_retraining(monkeypatch, sessions, runtime):
    monkeypatch.setattr(mlflow_tracking, "_OFFLINE", False)
    log_id, run_id = make_run(sessions)
    _stage(run_id)
    api = Accepting()

    result = registry_retry.retry_registration(log_id, api=api, session_factory=sessions)

    assert result["outcome"] == registry_retry.REGISTERED
    assert api.registered == ["RandomForest_retrain"]
    assert journal(sessions, log_id)["registry_ok"] is True
    # Only real numbers reach log_metrics: registry_ok is a bool, split_kind a string.
    assert api.metrics == {"roc_auc": 0.9063, "test_samples": 3400}


def test_a_second_call_after_success_does_not_register_again(monkeypatch, sessions, runtime):
    monkeypatch.setattr(mlflow_tracking, "_OFFLINE", False)
    log_id, run_id = make_run(sessions)
    _stage(run_id)
    registry_retry.retry_registration(log_id, api=Accepting(), session_factory=sessions)

    again = registry_retry.retry_registration(log_id, api=Tripwire(), session_factory=sessions)

    assert again["outcome"] == registry_retry.ALREADY_REGISTERED
    assert journal(sessions, log_id)["registry_ok"] is True


def test_a_pruned_candidate_is_refused_rather_than_registered(monkeypatch, sessions, runtime):
    """The run's staged model is gone (retention removed it); registering whatever
    else is on disk would file a different model under this run."""
    monkeypatch.setattr(mlflow_tracking, "_OFFLINE", False)
    log_id, run_id = make_run(sessions)
    # No _stage(): the directory never existed / was pruned.

    result = registry_retry.retry_registration(log_id, api=Tripwire(), session_factory=sessions)

    assert result["outcome"] == registry_retry.STAGED_MISSING
    assert result["run_id"] == run_id
    assert journal(sessions, log_id)["registry_ok"] is False, "the journal must not be rewritten"


def test_a_candidate_still_being_written_is_refused(monkeypatch, sessions, runtime):
    monkeypatch.setattr(mlflow_tracking, "_OFFLINE", False)
    log_id, run_id = make_run(sessions)
    _stage(run_id, complete=True, incomplete=True)

    result = registry_retry.retry_registration(log_id, api=Tripwire(), session_factory=sessions)

    assert result["outcome"] == registry_retry.STILL_WRITING
    assert journal(sessions, log_id)["registry_ok"] is False


def test_a_refused_upload_leaves_the_journal_saying_it_is_not_registered(monkeypatch, sessions, runtime):
    monkeypatch.setattr(mlflow_tracking, "_OFFLINE", False)
    log_id, run_id = make_run(sessions)
    _stage(run_id)

    class Refusing(Accepting):
        @property
        def sklearn(self):
            class _Sklearn:
                def log_model(self, model, name):
                    raise RuntimeError("PermissionError: '/mlflow'")

            return _Sklearn()

    result = registry_retry.retry_registration(log_id, api=Refusing(), session_factory=sessions)

    assert result["outcome"] == registry_retry.REGISTRATION_FAILED
    assert journal(sessions, log_id)["registry_ok"] is False


def test_a_row_without_a_model_version_is_refused(monkeypatch, sessions, runtime):
    """The closed loop's own row carries no model_version; only the training row does."""
    monkeypatch.setattr(mlflow_tracking, "_OFFLINE", False)
    log_id, run_id = make_run(sessions, model_version=None)
    _stage(run_id)

    result = registry_retry.retry_registration(log_id, api=Tripwire(), session_factory=sessions)

    assert result["outcome"] == registry_retry.NO_MODEL_VERSION


def test_a_row_without_a_run_id_is_refused(monkeypatch, sessions, runtime):
    monkeypatch.setattr(mlflow_tracking, "_OFFLINE", False)
    log_id, _ = make_run(sessions, run_id=None)

    result = registry_retry.retry_registration(log_id, api=Tripwire(), session_factory=sessions)

    assert result["outcome"] == registry_retry.NO_RUN_ID


def test_a_missing_row_is_reported(monkeypatch, sessions, runtime):
    monkeypatch.setattr(mlflow_tracking, "_OFFLINE", False)
    result = registry_retry.retry_registration(99999, api=Tripwire(), session_factory=sessions)
    assert result["outcome"] == registry_retry.NOT_FOUND


def test_the_endpoint_requires_admin_and_delegates_to_the_run(monkeypatch):
    """POST /api/v1/model/retrain/{id}/retry-registration: admin-only, passes the id."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.auth import require_admin

    client = TestClient(app)
    path = "/api/v1/model/retrain/7/retry-registration"

    anon = client.post(path)
    assert anon.status_code in (401, 403), "the retry endpoint must not be anonymous"

    seen = {}

    def _stub(log_id):
        seen["id"] = log_id
        return {"outcome": "registered", "retrain_log_id": log_id, "detail": "stub",
                "model_version": "v", "run_id": "r"}

    monkeypatch.setattr(registry_retry, "retry_registration", _stub)
    app.dependency_overrides[require_admin] = lambda: {"username": "t", "role": "admin"}
    try:
        ok = client.post(path)
    finally:
        app.dependency_overrides.pop(require_admin, None)

    assert ok.status_code == 200
    assert seen["id"] == 7
    assert ok.json()["outcome"] == "registered"


def test_offline_is_reported_as_a_failed_retry_not_as_success(monkeypatch, sessions, runtime):
    monkeypatch.setattr(mlflow_tracking, "_OFFLINE", True)
    log_id, run_id = make_run(sessions)
    _stage(run_id)

    result = registry_retry.retry_registration(log_id, api=Accepting(), session_factory=sessions)

    assert result["outcome"] == registry_retry.REGISTRATION_FAILED
    assert journal(sessions, log_id)["registry_ok"] is False
