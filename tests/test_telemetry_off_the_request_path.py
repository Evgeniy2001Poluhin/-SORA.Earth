"""MLflow telemetry must not be on the request path (#255).

`log_prediction` and `log_evaluation` were called synchronously inside request
handlers, and MLflow's client has no bounded wait: measured 2026-09-06 against
a port nothing serves, one metadata call took **247 seconds** on MLflow's
defaults, because `MLFLOW_HTTP_REQUEST_TIMEOUT` is 120s *per attempt* and the
retry policy multiplies it.

The scale was larger than one call per request, and that is what decided the
design. `calculate_esg` logs an evaluation itself, and
`POST /api/v1/evaluate/monte-carlo` calls it once per sample with `n = 500` by
default, so a single request created five hundred MLflow runs before
answering. FastAPI's `BackgroundTasks` can only be reached from a handler
signature; these calls happen several frames down, inside a loop. So the
dispatch lives in the telemetry layer instead, where every call site reaches
it.

Ten properties were asked for, and each has a test below. The two about
ordering are taken at the ASGI level rather than through `TestClient`, because
`TestClient` runs the whole application before returning and cannot observe
that the response was emitted first.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from app import telemetry


@pytest.fixture(autouse=True)
def clean_dispatcher():
    """Each test starts with an idle dispatcher and its own counters."""
    telemetry.drain(5)
    with telemetry._lock:
        for key in telemetry._counts:
            telemetry._counts[key] = 0
        telemetry._shutting_down = False
    yield
    telemetry.drain(5)
    with telemetry._lock:
        telemetry._shutting_down = False


# --- 1 and 9: the response goes first, and the telemetry still runs ---------


def _asgi_timeline(handler_delay: float, telemetry_delay: float):
    """Drive a minimal ASGI app and record when each thing happened.

    Returns `(response_sent_at, telemetry_finished_at)` on one clock. A
    `TestClient` cannot answer this: httpx waits for the application to
    finish, so "the response came first" and "the response came last" produce
    the same observation there -- the shape of a check that cannot fail.
    """
    ran = threading.Event()
    telemetry_finished_at: list[float] = []
    response_sent_at: list[float] = []

    def slow_telemetry():
        time.sleep(telemetry_delay)
        telemetry_finished_at.append(time.monotonic())
        ran.set()

    async def app(scope, receive, send):
        await asyncio.sleep(handler_delay)
        telemetry.submit("probe", slow_telemetry)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    async def drive():
        async def send(message):
            if message["type"] == "http.response.body":
                response_sent_at.append(time.monotonic())

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        await app({"type": "http", "method": "POST", "path": "/x"}, receive, send)

    asyncio.run(drive())
    assert ran.wait(10), "the telemetry task never ran"
    return response_sent_at[0], telemetry_finished_at[0]


def test_the_response_is_sent_before_the_telemetry_finishes():
    """Point 1: the response does not wait for MLflow."""
    response_at, telemetry_at = _asgi_timeline(handler_delay=0.0, telemetry_delay=0.3)

    assert response_at < telemetry_at, (
        f"the response waited for telemetry: response at {response_at:.3f}, "
        f"telemetry finished at {telemetry_at:.3f}"
    )


def test_the_telemetry_actually_runs_and_is_not_silently_dropped():
    """Point 9: off the request path is not the same as never.

    Asserted separately from the ordering above, which a dispatcher that
    dropped everything would also satisfy -- perfectly, and for the wrong
    reason.
    """
    ran = threading.Event()

    telemetry.submit("probe", ran.set)

    assert telemetry.drain(5) == 0
    assert ran.is_set(), "the task was accepted and never executed"
    assert telemetry.stats()["succeeded"] == 1


# --- 2: a telemetry failure does not change the response -------------------


def test_a_failing_task_does_not_reach_the_caller():
    """Point 2: submit never raises, whatever the task does."""
    def explode():
        raise RuntimeError("MLflow is down")

    assert telemetry.submit("probe", explode) is True  # accepted, not raised
    assert telemetry.drain(5) == 0
    assert telemetry.stats()["failed"] == 1


def test_even_a_baseexception_in_a_task_stays_inside_it():
    # `except Exception` would let a KeyboardInterrupt or a test's own
    # BaseException-derived sentinel escape into a telemetry thread and be lost
    # with no counter moving.
    class Sentinel(BaseException):
        pass

    def explode():
        raise Sentinel()

    telemetry.submit("probe", explode)

    assert telemetry.drain(5) == 0
    assert telemetry.stats()["failed"] == 1


# --- 3: only values cross the boundary -------------------------------------


def test_the_dispatched_arguments_are_plain_values():
    """Point 3: nothing with a lifecycle is handed to a background thread.

    A request, a database session or an open connection outlives its scope
    here and would be touched after the response was sent. This checks the
    arguments the application's own call sites actually pass, by capturing
    them at the boundary rather than by reading the call sites.
    """
    captured: list[tuple] = []

    def capturing(name, fn, *args, **kwargs):
        # Captures and does **not** dispatch. The subject is what crosses the
        # boundary, not what happens afterwards; running the real work here
        # would contact MLflow, which the first version of this test did --
        # two minutes of the suite and a warning leaking into the next test.
        captured.append((name, args, kwargs))
        return True

    import app.mlflow_tracking as m

    original_offline = m._OFFLINE
    try:
        m._OFFLINE = False
        m.telemetry = type("_", (), {"submit": staticmethod(capturing)})()
        m.log_prediction("rf_v1", {"budget": 1, "co2_reduction": 2}, prediction=1)
        m.log_evaluation("P", {"total_score": 70.0}, "Low")
    finally:
        m.telemetry = telemetry
        m._OFFLINE = original_offline

    assert len(captured) == 2, captured
    for name, args, kwargs in captured:
        for value in list(args) + list(kwargs.values()):
            if value is None or hasattr(value, "model_dump"):
                continue  # a Pydantic model is a value, not a live handle
            json.dumps(value)  # raises for anything holding a connection


# --- 4: failures are aggregated, not narrated ------------------------------


def test_a_burst_of_the_same_failure_is_reported_once(caplog):
    """Point 4: five hundred identical warnings is not a log anyone reads."""
    import app.mlflow_tracking as m

    m._reset_failure_reporting()
    with caplog.at_level("WARNING", logger="app.mlflow_tracking"):
        for _ in range(50):
            m._log_mlflow_failure("log_evaluation", RuntimeError("down"))

    reports = [r for r in caplog.records if "log_evaluation" in r.getMessage()]
    assert len(reports) == 1, f"{len(reports)} reports for one burst"
    m._reset_failure_reporting()


def test_a_different_failure_is_still_reported_promptly(caplog):
    """The rate limit is per operation: a new kind must not wait a minute."""
    import app.mlflow_tracking as m

    m._reset_failure_reporting()
    with caplog.at_level("WARNING", logger="app.mlflow_tracking"):
        m._log_mlflow_failure("log_evaluation", RuntimeError("down"))
        m._log_mlflow_failure("log_prediction", RuntimeError("down"))

    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "log_evaluation" in messages
    assert "log_prediction" in messages
    m._reset_failure_reporting()


def test_the_suppressed_count_is_reported_and_not_lost(caplog):
    import app.mlflow_tracking as m

    m._reset_failure_reporting()
    original = m._FAILURE_LOG_INTERVAL
    try:
        m._FAILURE_LOG_INTERVAL = 0.05
        with caplog.at_level("WARNING", logger="app.mlflow_tracking"):
            for _ in range(4):
                m._log_mlflow_failure("log_evaluation", RuntimeError("down"))
            time.sleep(0.06)
            m._log_mlflow_failure("log_evaluation", RuntimeError("down"))
    finally:
        m._FAILURE_LOG_INTERVAL = original
        m._reset_failure_reporting()

    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "and 3 more" in joined, joined


# --- 5: four outcomes, every one of them reachable -------------------------


def test_dropped_increments_when_the_bound_is_reached():
    """Point 5: `dropped` is not decoration.

    A counter that cannot move is the defect this repository keeps finding --
    `legacy_hash_count()` returning a structurally impossible zero. So the
    drop path is exercised, not merely declared.
    """
    release = threading.Event()
    original = telemetry._MAX_IN_FLIGHT
    try:
        telemetry._MAX_IN_FLIGHT = 2
        accepted = [telemetry.submit("probe", release.wait) for _ in range(5)]
    finally:
        release.set()
        telemetry._MAX_IN_FLIGHT = original
    telemetry.drain(5)

    assert accepted.count(True) == 2, accepted
    assert accepted.count(False) == 3, accepted
    assert telemetry.stats()["dropped"] == 3
    assert telemetry.stats()["scheduled"] == 2


def test_every_outcome_is_counted():
    stats_before = telemetry.stats()
    telemetry.submit("probe", lambda: None)
    telemetry.submit("probe", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    telemetry.drain(5)
    stats = telemetry.stats()

    assert stats["scheduled"] == stats_before["scheduled"] + 2
    assert stats["succeeded"] == stats_before["succeeded"] + 1
    assert stats["failed"] == stats_before["failed"] + 1


def test_a_work_function_that_reports_its_own_failure_still_counts_as_failed():
    """The MLflow functions catch their own error and return False.

    Without the dispatcher reading that, `failed` could only ever count
    escapes -- which is to say almost never, since those functions never let
    one out.
    """
    telemetry.submit("probe", lambda: False)

    assert telemetry.drain(5) == 0
    assert telemetry.stats()["failed"] == 1
    assert telemetry.stats()["succeeded"] == 0


# --- 6: nothing is retried -------------------------------------------------


def test_a_failing_task_runs_exactly_once():
    """Point 6: no unbounded retries -- no retries at all.

    A tracking server that refused this event will refuse the retry, and the
    request it belonged to is long gone.
    """
    attempts = []

    def failing():
        attempts.append(1)
        raise RuntimeError("down")

    telemetry.submit("probe", failing)
    telemetry.drain(5)
    time.sleep(0.2)  # a retry would land in this window

    assert len(attempts) == 1, f"{len(attempts)} attempts"


# --- 7: loss at shutdown is admitted ---------------------------------------


def test_shutdown_reports_what_it_lost_and_does_not_raise(caplog):
    """Point 7: best effort, stated as such.

    This is not a durable queue and must not be mistaken for one, so the loss
    is counted and logged rather than passing quietly.
    """
    release = threading.Event()
    telemetry.submit("probe", release.wait)

    original = telemetry._SHUTDOWN_GRACE_SECONDS
    try:
        telemetry._SHUTDOWN_GRACE_SECONDS = 0.1
        with caplog.at_level("WARNING", logger="app.telemetry"):
            telemetry.shutdown()      # must not raise
    finally:
        telemetry._SHUTDOWN_GRACE_SECONDS = original
        release.set()
        telemetry.drain(5)
        with telemetry._lock:
            telemetry._shutting_down = False
            telemetry._executor = None

    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "dropped at shutdown" in joined, joined
    assert "not a durable queue" in joined, "the log must not imply durability"


def test_nothing_is_accepted_after_shutdown():
    try:
        with telemetry._lock:
            telemetry._shutting_down = True
        assert telemetry.submit("probe", lambda: None) is False
        assert telemetry.stats()["dropped"] == 1
    finally:
        with telemetry._lock:
            telemetry._shutting_down = False


# --- 8: an unresponsive MLflow does not slow the response ------------------


def test_a_247_second_task_does_not_delay_the_response():
    """Point 8: the measured worst case, stood in for by a blocking task.

    247 seconds is what one MLflow metadata call cost against a dead port. The
    task here blocks until released rather than actually dialling out: the
    property under test is that the response does not wait, and making the
    test itself wait four minutes to prove it would be its own defect.
    """
    release = threading.Event()
    finished = threading.Event()

    def blocked():
        release.wait(30)
        finished.set()

    async def app(scope, receive, send):
        telemetry.submit("probe", blocked)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    async def drive():
        async def send(message):
            pass

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        await app({"type": "http", "method": "POST", "path": "/x"}, receive, send)

    started = time.monotonic()
    asyncio.run(drive())
    elapsed = time.monotonic() - started

    assert not finished.is_set(), "the task completed, so it never blocked"
    assert elapsed < 1.0, f"the request took {elapsed:.2f}s while telemetry was stuck"

    release.set()
    telemetry.drain(5)


# --- 10: the long operations keep their own timeouts -----------------------


def test_model_registration_is_still_synchronous():
    """Point 10: registration is not telemetry.

    Its return value gates promotion -- `False` means the model is not in the
    registry and must not be promoted (#189) -- so it cannot be dispatched,
    and its timeout is not this module's business.
    """
    import inspect

    import app.mlflow_tracking as m

    source = inspect.getsource(m.log_model_registry)
    assert "telemetry.submit" not in source, (
        "log_model_registry was dispatched; the promotion gate reads its return value"
    )
    assert "return False" in source


def test_the_global_mlflow_timeouts_are_untouched():
    """Point 10, the other half: nothing here shortens artefact uploads."""
    import os

    import app.mlflow_tracking as m

    # The bound exists, and it is applied around the experiment lookup only --
    # asserted in tests/test_mlflow_import_makes_no_request.py. What must hold
    # here is that importing and using the dispatcher leaves no bound behind.
    telemetry.submit("probe", lambda: None)
    telemetry.drain(5)

    for key in m._EXPERIMENT_LOOKUP_ENV:
        assert os.environ.get(key) is None or os.environ.get(key) != m._EXPERIMENT_LOOKUP_ENV[key], (
            f"{key} was left at the lookup bound, which also covers artefact uploads"
        )


def test_the_prometheus_counter_separates_the_two_kinds_of_drop():
    """`dropped` alone is not readable, and an unreadable counter is not a signal.

    Measured 2026-09-06: one `POST /evaluate/monte-carlo` submits 500
    `log_evaluation` tasks in about a millisecond -- faster than any worker can
    take them -- so 94% are shed whatever MLflow's latency is. Without the
    operation label, that noise buries a dropped `log_prediction`, which is the
    one that means a real request's telemetry was lost.
    """
    from prometheus_client import REGISTRY

    def value(outcome: str, operation: str) -> float:
        got = REGISTRY.get_sample_value(
            "sora_telemetry_tasks_total",
            {"outcome": outcome, "operation": operation},
        )
        return got or 0.0

    before = value("scheduled", "probe_labelled")
    telemetry.submit("probe_labelled", lambda: None)
    assert telemetry.drain(5) == 0

    assert value("scheduled", "probe_labelled") == before + 1
    assert value("scheduled", "a_name_never_used") == 0.0, (
        "the label is not being applied; every operation shares one series"
    )
