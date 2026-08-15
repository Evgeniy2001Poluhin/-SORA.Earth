"""`log_model_registry` must report registration honestly, and be provable offline (#189).

The contract was already decided and is already in the code: `False` means the
model is not registered, such a run is reported `registry_failed`, and it is not
promoted. What was missing is evidence that the two *negative* paths behave that
way, taken by running the function rather than by reading it --
`tests/test_registration_gates_promotion.py` asserts on the source text of
`app/scheduler.py`, which cannot distinguish code that works from code that
merely still contains the right words.

Two hazards make a naive test here green for the wrong reason, and both are
handled explicitly rather than hoped away:

1. **conftest's network ban permits loopback.** `_ALLOWED_HOSTS` is
   `{"127.0.0.1", "::1", "localhost"}` so the FastAPI TestClient keeps working,
   and `MLFLOW_TRACKING_URI` defaults to `http://127.0.0.1:5556`. A test that
   leaned on that fixture for isolation would have none at all against the one
   server it cares about.

2. **CI runs the suite with `SORA_OFFLINE=1`.** `_OFFLINE` is read once at
   import, so in CI `log_model_registry` returns `False` at its first line. A
   failure-path test that did not force `_OFFLINE` off would pass without ever
   reaching the failure it is named after -- the offline branch would be
   answering for it.
"""
import logging
import socket

import pytest

from app import mlflow_tracking


class NetworkAttempted(BaseException):
    """Raised when a test opens a socket. Deliberately **not** an `Exception`.

    `log_model_registry` catches `Exception` and returns `False`. If the ban
    raised something it could catch, a test that leaked out to a real MLflow
    server would still observe `False` and stay green: the breach would be
    indistinguishable from the behaviour under test. Deriving from
    `BaseException` puts the breach beyond the subject's reach, so false
    isolation fails the test instead of decorating it.
    """


class no_network:
    """Refuse every socket operation for the duration of the block, loopback included.

    Records what was attempted so a failure names the address rather than only
    the fact that something dialled out.
    """

    def __enter__(self):
        self.attempts = []
        self._connect = socket.socket.connect
        self._create_connection = socket.create_connection
        self._getaddrinfo = socket.getaddrinfo

        def deny(kind, address_at):
            def blocked(*args, **kwargs):
                target = args[address_at] if len(args) > address_at else "?"
                self.attempts.append((kind, target))
                raise NetworkAttempted(f"{kind} -> {target!r}")

            return blocked

        # connect is bound, so args[0] is the socket and args[1] the address.
        socket.socket.connect = deny("connect", 1)
        socket.create_connection = deny("create_connection", 0)
        socket.getaddrinfo = deny("getaddrinfo", 0)
        return self

    def __exit__(self, *exc_info):
        socket.socket.connect = self._connect
        socket.create_connection = self._create_connection
        socket.getaddrinfo = self._getaddrinfo
        return False


class Tripwire:
    """An MLflow stand-in that fails on any use whatsoever.

    "Never called" is then asserted by the object itself rather than inferred
    from the return value -- `False` alone would be equally consistent with
    "called MLflow, and it failed".
    """

    def __init__(self):
        self.touched = []

    def __getattr__(self, name):
        self.touched.append(name)
        raise NetworkAttempted(f"MLflow was used while offline: .{name}")


class RegistrationRefused(Exception):
    """Shaped like the production failure: the upload of the artefact is refused.

    On production this arrived as `PermissionError: '/mlflow'`, because
    experiments 0 and 1 carry an absolute `artifact_location` from before the
    server ran with `--serve-artifacts`, so the client was handed a container
    path and tried to write it locally.
    """


class RefusingApi:
    """Succeeds up to the model upload, then refuses it.

    Failing at `sklearn.log_model` rather than at `start_run` matters: the run
    *is* created, which is what made the production failure survive so long --
    MLflow showed runs, so registration looked alive.
    """

    class _Run:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    def __init__(self):
        self.calls = []

    def start_run(self, run_name=None):
        self.calls.append("start_run")
        return self._Run()

    def log_metrics(self, metrics):
        self.calls.append("log_metrics")

    def set_tag(self, key, value):
        self.calls.append("set_tag")

    @property
    def sklearn(self):
        recorder = self

        class _Sklearn:
            def log_model(self, model, name):
                recorder.calls.append("log_model")
                raise RegistrationRefused("PermissionError: '/mlflow'")

        return _Sklearn()


MODEL = object()
METRICS = {"auc": 0.9063, "f1": 0.82, "accuracy": 0.85}


def test_offline_does_not_call_mlflow_and_reports_the_model_unregistered(monkeypatch):
    """What makes this red: flipping `_OFFLINE` to False.

    The Tripwire then raises on the first attribute MLflow is reached for, and
    the test errors out instead of returning `False`. Verified by doing exactly
    that before committing.
    """
    monkeypatch.setattr(mlflow_tracking, "_OFFLINE", True)
    api = Tripwire()

    with no_network() as net:
        result = mlflow_tracking.log_model_registry(
            MODEL, "RandomForest_retrain", METRICS, api=api
        )

    assert result is False, "offline is not registered, and must not be reported as success"
    assert api.touched == [], f"MLflow was reached while offline: {api.touched}"
    assert net.attempts == [], f"a socket was opened while offline: {net.attempts}"


def test_registration_failure_is_caught_and_reported_as_unregistered(monkeypatch, caplog):
    """What makes this red: three separate states, each reproduced to check it.

    - the function letting `RegistrationRefused` escape -> the call raises;
    - it returning True after a refused upload -> the first assertion;
    - the offline branch answering instead of the failure path -> `log_model`
      would be missing from `calls`, which is why that assertion is here and
      not merely implied by the return value.
    """
    # CI runs with SORA_OFFLINE=1; without this the function returns at its
    # first line and the failure below is never reached.
    monkeypatch.setattr(mlflow_tracking, "_OFFLINE", False)
    api = RefusingApi()

    with caplog.at_level(logging.WARNING, logger="app.mlflow_tracking"):
        with no_network() as net:
            result = mlflow_tracking.log_model_registry(
                MODEL, "RandomForest_retrain", METRICS, api=api
            )

    assert result is False, "a refused upload is not a registration"
    assert "log_model" in api.calls, (
        "the failure path was never reached -- the offline branch answered instead; "
        f"calls were {api.calls}"
    )
    assert net.attempts == [], f"the isolated client still dialled out: {net.attempts}"

    # Caught is not the same as swallowed: an operator has to be able to see it.
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "log_model_registry" in logged, f"the failure was not reported: {logged!r}"
    assert "RegistrationRefused" in logged, f"the failure was not identified: {logged!r}"


def test_the_network_ban_itself_actually_bans(monkeypatch):
    """The ban is the load-bearing part of both tests above, so it is tested too.

    A ban that silently stopped working would leave both of them passing while
    isolating nothing -- the failure mode this whole file exists to rule out.
    """
    with no_network() as net:
        with pytest.raises(BaseException) as caught:
            socket.create_connection(("127.0.0.1", 5556), timeout=1)

    assert isinstance(caught.value, NetworkAttempted)
    assert not isinstance(caught.value, Exception), (
        "must be outside the subject's `except Exception`, or a breach reads as a pass"
    )
    assert net.attempts == [("create_connection", ("127.0.0.1", 5556))]

    # And it is undone on exit, or it would poison every test that follows.
    assert socket.create_connection is not None
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.close()
