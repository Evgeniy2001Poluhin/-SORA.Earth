"""The two callers of `app.api.drift` that are not HTTP.

Declaring the endpoint's contract changed what `check_drift` returns -- a
Pydantic model, or a `JSONResponse` on the unavailable path. Both callers
inside the application still did `drift_result.get("drift_detected", False)`.
`.get` is a dict method; neither a model nor a Response has one, so the closed
loop and `POST /mlops/auto-retrain` would have raised `AttributeError` the next
time they ran.

**Nothing caught it**, and the reason is the point of this file: every existing
test patches `app.api.drift.check_drift` with a mock returning a dict.
Replacing the function hides any disagreement between what it really returns
and what its caller expects. So these tests call the real `compute_drift` and
never mock it.

The second defect is quieter and was always there: both callers read a failed
drift check as "no drift". For the closed loop that means silently declining to
retrain whenever the KS test is broken -- which looks exactly like a healthy
model.
"""
from __future__ import annotations

import importlib
import sys
import types

import pytest

_mlflow_stub = types.ModuleType("app.mlflow_tracking")
_mlflow_stub.log_drift_event = lambda *a, **k: None  # type: ignore[attr-defined]
_mlflow_stub.log_prediction = lambda *a, **k: None  # type: ignore[attr-defined]
_mlflow_stub.log_evaluation = lambda *a, **k: None  # type: ignore[attr-defined]
_mlflow_stub.get_experiment_stats = lambda *a, **k: {}  # type: ignore[attr-defined]
sys.modules.setdefault("app.mlflow_tracking", _mlflow_stub)

drift_module = importlib.import_module("app.api.drift")


@pytest.fixture()
def unavailable(monkeypatch):
    """Make the KS test impossible: SciPy absent."""
    monkeypatch.setattr(drift_module, "HAS_SCIPY", False)


@pytest.fixture()
def no_log(monkeypatch, tmp_path):
    """A working install with nothing to compare yet."""
    baseline = tmp_path / "projects.csv"
    baseline.write_text("budget,co2_reduction,social_impact,duration_months\n1,2,3,4\n", encoding="utf-8")
    monkeypatch.setattr(drift_module, "PROJ_CSV", str(baseline))
    monkeypatch.setattr(drift_module, "PRED_LOG", str(tmp_path / "absent.csv"))


def test_compute_drift_returns_a_value_on_every_branch(unavailable):
    """Not a Response. The HTTP wrapper is what turns it into one."""
    from fastapi.responses import JSONResponse

    result = drift_module.compute_drift(window=50)

    assert not isinstance(result, JSONResponse)
    assert result.status == "unavailable"
    assert result.reason_code == "scipy_missing"


def test_the_http_wrapper_still_answers_503(unavailable):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(drift_module.router, prefix="/api/v1")
    client = TestClient(app, raise_server_exceptions=False)

    r = client.get("/api/v1/model/drift")

    assert r.status_code == 503
    assert r.json()["status"] == "unavailable"


@pytest.mark.parametrize("fixture_name", ["unavailable", "no_log"])
def test_the_closed_loop_survives_every_drift_result(request, fixture_name, monkeypatch):
    """The regression: `.get` on a model raised AttributeError.

    Runs the real `compute_drift` -- no mock -- so a future change to what it
    returns fails here rather than in production twelve hours later.
    """
    request.getfixturevalue(fixture_name)
    scheduler = importlib.import_module("app.scheduler")

    class FreeLock:
        def __init__(self, **_kw):
            pass

        def acquire(self):
            return True

        def release(self):
            return None

    monkeypatch.setattr("app.locks.RedisLock", FreeLock)

    result = scheduler.closed_loop_retrain(trigger_source="test")

    assert isinstance(result, dict)
    assert result["retrained"] is False


def test_the_closed_loop_does_not_read_a_failed_check_as_no_drift(unavailable, monkeypatch):
    """The quieter defect. "The check broke" is not "the model is fine"."""
    scheduler = importlib.import_module("app.scheduler")

    class FreeLock:
        def __init__(self, **_kw):
            pass

        def acquire(self):
            return True

        def release(self):
            return None

    monkeypatch.setattr("app.locks.RedisLock", FreeLock)

    result = scheduler.closed_loop_retrain(trigger_source="test")

    assert result["reason"] == "drift_check_unavailable"
    assert result["drift_detected"] is None, "False would claim drift was looked for"
    assert result["status"] != "ok", "an unavailable check is not an ok cycle"
