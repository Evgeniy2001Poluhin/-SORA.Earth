"""MLflow failures must be reported, not swallowed.

All four MLflow call sites caught Exception and passed. Every one of them is
behind an `if _OFFLINE: return None`, so they were silent only when online --
that is, only in production. For get_experiment_stats in particular an outage
and an empty experiment both produced total_runs = 0, which made them
indistinguishable.

MLflow is optional telemetry, so a failure must stay non-fatal. It must also not
put a tracking URI, a query string or a credential into the log.
"""
from unittest.mock import patch

import pytest

from app import mlflow_tracking


PROJECT = {"total_score": 72.0, "environment_score": 70.0, "social_score": 74.0,
           "economic_score": 71.0, "success_probability": 0.8}


class Boom(RuntimeError):
    """Distinct class so the assertions can look for it by name."""


@pytest.fixture
def online(monkeypatch):
    """Force the online path; the offline early-return is tested separately."""
    monkeypatch.setattr(mlflow_tracking, "_OFFLINE", False)


def test_sanitizer_removes_uri_userinfo():
    out = mlflow_tracking._sanitized(Boom("connect to http://user:hunter2@mlflow:5556 failed"))
    assert "hunter2" not in out
    assert "[redacted]" in out


def test_sanitizer_removes_query_string_and_credentials():
    out = mlflow_tracking._sanitized(Boom("GET /api?token=abc123&x=1 failed"))
    assert "abc123" not in out
    assert "[redacted]" in out

    out = mlflow_tracking._sanitized(Boom("api_key=zzz rejected"))
    assert "zzz" not in out


def test_sanitizer_neutralizes_control_characters():
    out = mlflow_tracking._sanitized(Boom("line one\nMLflow log_prediction failed: forged"))
    assert "\n" not in out, "a failure must not be able to forge a second log line"


def test_sanitizer_truncates_an_excessive_message():
    out = mlflow_tracking._sanitized(Boom("x" * 5000))
    assert len(out) <= mlflow_tracking._MAX_DETAIL + 3
    assert out.endswith("...")


@pytest.mark.parametrize(
    "operation, call",
    [
        ("log_prediction", lambda: mlflow_tracking.log_prediction("rf_v1", {"budget": 1}, prediction=1)),
        ("log_evaluation", lambda: mlflow_tracking.log_evaluation("P", PROJECT, "Low")),
        ("log_model_registry", lambda: mlflow_tracking.log_model_registry(object(), "rf_v1", {"auc": 0.9})),
    ],
)
def test_failure_is_logged_and_non_fatal(operation, call, online, caplog):
    with patch.object(mlflow_tracking.mlflow, "start_run", side_effect=Boom("upstream is down")):
        with caplog.at_level("WARNING", logger="app.mlflow_tracking"):
            call()  # must not raise

    messages = [r.getMessage() for r in caplog.records]
    assert any(operation in m and "Boom" in m for m in messages), messages


def test_get_experiment_stats_distinguishes_failure_from_an_empty_experiment(online, caplog):
    with patch.object(mlflow_tracking.mlflow, "get_experiment_by_name", side_effect=Boom("down")):
        with caplog.at_level("WARNING", logger="app.mlflow_tracking"):
            stats = mlflow_tracking.get_experiment_stats()

    assert stats["total_runs"] == 0
    assert stats.get("_mlflow_error") == "Boom", "an outage must be distinguishable from empty"
    assert any("get_experiment_stats" in r.getMessage() for r in caplog.records)


def test_successful_path_logs_no_warning(online, caplog):
    with patch.object(mlflow_tracking.mlflow, "get_experiment_by_name", return_value=None):
        with caplog.at_level("WARNING", logger="app.mlflow_tracking"):
            stats = mlflow_tracking.get_experiment_stats()

    assert stats["total_runs"] == 0
    assert "_mlflow_error" not in stats, "an empty experiment is not an error"
    assert not [r for r in caplog.records if "MLflow" in r.getMessage()]


def test_offline_mode_never_calls_mlflow(monkeypatch):
    monkeypatch.setattr(mlflow_tracking, "_OFFLINE", True)

    with patch.object(mlflow_tracking.mlflow, "start_run", side_effect=AssertionError("called")):
        assert mlflow_tracking.log_prediction("rf_v1", {"budget": 1}) is None
        assert mlflow_tracking.log_evaluation("P", PROJECT, "Low") is None
        assert mlflow_tracking.log_model_registry(object(), "rf_v1", {}) is None


def test_no_payload_or_model_contents_are_logged(online, caplog):
    secret_payload = {"budget": 123456789, "api_key": "super-secret-value"}

    with patch.object(mlflow_tracking.mlflow, "start_run", side_effect=Boom("failed")):
        with caplog.at_level("WARNING", logger="app.mlflow_tracking"):
            mlflow_tracking.log_prediction("rf_v1", secret_payload, prediction=1)

    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "super-secret-value" not in joined
    assert "123456789" not in joined
