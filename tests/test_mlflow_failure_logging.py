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
    """Force the online path; the offline early-return is tested separately.

    `_experiment_ready` is set for the same reason `_OFFLINE` is cleared: to
    put the module in the state these tests are about. Resolving the
    experiment used to happen at import and now happens on first use (#243),
    so without this every call below would first try to reach a tracking
    server that is not running -- adding a network round trip and a
    "not resolved" warning to tests whose subject is what `start_run` does.
    The lazy resolution itself is covered by
    `tests/test_mlflow_import_makes_no_request.py` and
    `tests/test_mlflow_registry_isolation.py`.
    """
    monkeypatch.setattr(mlflow_tracking, "_OFFLINE", False)
    monkeypatch.setattr(mlflow_tracking, "_experiment_ready", True)


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

    # No field is added to the result: this dict is returned verbatim by
    # GET /api/v1/mlflow/stats, so the warning is the signal, not the payload.
    assert stats["total_runs"] == 0
    assert "_mlflow_error" not in stats, "the public response contract must not change"
    assert any("get_experiment_stats" in r.getMessage() and "Boom" in r.getMessage()
               for r in caplog.records)


def test_successful_path_logs_no_warning(online, caplog):
    with patch.object(mlflow_tracking.mlflow, "get_experiment_by_name", return_value=None):
        with caplog.at_level("WARNING", logger="app.mlflow_tracking"):
            stats = mlflow_tracking.get_experiment_stats()

    assert stats["total_runs"] == 0
    assert not [r for r in caplog.records if "MLflow" in r.getMessage()]


def test_offline_mode_never_calls_mlflow(monkeypatch):
    monkeypatch.setattr(mlflow_tracking, "_OFFLINE", True)

    with patch.object(mlflow_tracking.mlflow, "start_run", side_effect=AssertionError("called")):
        assert mlflow_tracking.log_prediction("rf_v1", {"budget": 1}) is None
        assert mlflow_tracking.log_evaluation("P", PROJECT, "Low") is None
        # False, not None, since #189. The other two report nothing because
        # nothing depends on whether they ran. Registration is different: the
        # promotion gate refuses a model that is not in the registry, and
        # "MLflow is switched off" is one of the ways a model is not in it.
        # Returning None there would leave the gate unable to tell "not
        # registered" from "no opinion".
        assert mlflow_tracking.log_model_registry(object(), "rf_v1", {}) is False

    # get_experiment_stats is the path the offline guard was added to, so it has
    # to be covered too: the lookup must not happen at all.
    with patch.object(mlflow_tracking.mlflow, "get_experiment_by_name",
                      side_effect=AssertionError("MLflow was contacted while offline")):
        stats = mlflow_tracking.get_experiment_stats()
    assert stats["total_runs"] == 0
    assert stats["experiment"] == mlflow_tracking.EXPERIMENT_NAME


def test_no_payload_or_model_contents_are_logged(online, caplog):
    secret_payload = {"budget": 123456789, "api_key": "super-secret-value"}

    with patch.object(mlflow_tracking.mlflow, "start_run", side_effect=Boom("failed")):
        with caplog.at_level("WARNING", logger="app.mlflow_tracking"):
            mlflow_tracking.log_prediction("rf_v1", secret_payload, prediction=1)

    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "super-secret-value" not in joined
    assert "123456789" not in joined


@pytest.mark.parametrize("message, secret", [
    ("api_key: super-secret", "super-secret"),
    ("token: abc123", "abc123"),
    ("password:hunter2", "hunter2"),
    ('"api_key": "json-secret"', "json-secret"),
    ("SECRET = Caps", "Caps"),
])
def test_sanitizer_redacts_colon_and_quoted_separators(message, secret):
    """= is not the only separator these appear with in the wild."""
    assert secret not in mlflow_tracking._sanitized(Boom(message))


def test_sanitizer_handles_ipv6_and_schemeless_uris():
    out = mlflow_tracking._sanitized(Boom("connect to http://u:p@[::1]:5556/api failed"))
    assert "u:p@" not in out
    out = mlflow_tracking._sanitized(Boom("connect to //user:pw@host/path failed"))
    assert "user:pw" not in out


def test_sanitizer_survives_an_exception_whose_str_raises():
    class Hostile(Exception):
        def __str__(self):
            raise ValueError("no string for you")

    assert mlflow_tracking._sanitized(Hostile()) == "<unprintable>"


def test_sanitizer_flattens_a_multiline_message():
    out = mlflow_tracking._sanitized(Boom("first line\nsecond line\r\nthird"))
    assert "\n" not in out and "\r" not in out


def test_warning_arguments_carry_no_exception_object(online, caplog):
    """Only strings reach the logger, so no handler can re-render the exception."""
    with patch.object(mlflow_tracking.mlflow, "start_run", side_effect=Boom("x")):
        with caplog.at_level("WARNING", logger="app.mlflow_tracking"):
            mlflow_tracking.log_prediction("rf_v1", {"budget": 1})

    record = next(r for r in caplog.records if "MLflow" in r.getMessage())
    assert all(isinstance(a, str) for a in record.args), record.args
    assert record.exc_info is None, "a traceback can carry the URI and request details"


@pytest.mark.parametrize("message, secret", [
    ('api_key: "super secret value"', "secret value"),
    ("password: 'two words here'", "words here"),
    ('"api_key": "json secret value"', "secret value"),
    ('token="escaped \\" quote inside"', "quote inside"),
])
def test_sanitizer_takes_a_quoted_secret_whole(message, secret):
    """Stopping at whitespace left the tail of a quoted credential in the log."""
    out = mlflow_tracking._sanitized(Boom(message))
    assert secret not in out, out
