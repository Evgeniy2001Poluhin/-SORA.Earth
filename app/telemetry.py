"""Fire-and-forget dispatch for MLflow telemetry, so a request never waits on it (#255).

MLflow is optional observability. It was being called **synchronously inside
request handlers**, and its client has no bounded wait: measured 2026-09-06
against a port nothing serves, one metadata call took **247 seconds** on
MLflow's defaults -- `MLFLOW_HTTP_REQUEST_TIMEOUT` is 120s *per attempt* and
the retry policy multiplies it.

The scale is worse than one call per request. `calculate_esg` logs an
evaluation itself, and `POST /api/v1/evaluate/monte-carlo` calls it once per
sample with `n = 500` by default -- so a single request created five hundred
MLflow runs, in line, before answering. That is why this lives in the telemetry
layer rather than in the route handlers: FastAPI's `BackgroundTasks` can only
be reached from a handler signature, and these calls happen several frames
down, inside a loop.

**This is best effort, not a queue.** Nothing here is durable. A task in flight
when the process stops is lost, and one submitted while the dispatcher is
saturated is dropped rather than queued. Both are counted, because a dropped
telemetry event that nobody counts is indistinguishable from one that never
happened -- which is the failure shape this repository keeps finding. If any of
this ever becomes an audit record, it needs a real queue and this module is not
it.
"""
from __future__ import annotations

import atexit
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from app.prom_metrics import sora_telemetry_tasks_total

logger = logging.getLogger(__name__)

#: How many telemetry tasks may be outstanding before further ones are dropped.
#:
#: A bound, not a queue depth. `ThreadPoolExecutor`'s own queue is unbounded, so
#: submitting without this would turn a 500-sample monte-carlo against a dead
#: MLflow into five hundred parked tasks and the memory they hold. Dropping is
#: the correct answer for observability data: the newest events are worth more
#: than a backlog of stale ones, and the drop is counted.
_MAX_IN_FLIGHT = int(os.getenv("SORA_TELEMETRY_MAX_IN_FLIGHT", "32"))

#: Threads doing the actual MLflow calls. Small: they exist to keep the request
#: path free, not to make telemetry fast.
_WORKERS = int(os.getenv("SORA_TELEMETRY_WORKERS", "4"))

#: How long shutdown waits for outstanding tasks before giving up on them.
_SHUTDOWN_GRACE_SECONDS = float(os.getenv("SORA_TELEMETRY_SHUTDOWN_GRACE", "2.0"))

#: Minimum seconds between failure reports. Failures arrive in bursts -- a
#: tracking server is down for everybody at once -- and one traceback per
#: sample would be five hundred of them for a single request.
_FAILURE_LOG_INTERVAL = float(os.getenv("SORA_TELEMETRY_LOG_INTERVAL", "60"))

_lock = threading.Lock()
_executor: ThreadPoolExecutor | None = None
_in_flight = 0
_shutting_down = False

#: Local mirror of the Prometheus counters. `/metrics` is the interface; these
#: exist because a test asserting on a global registry is testing the registry.
_counts = {"scheduled": 0, "succeeded": 0, "failed": 0, "dropped": 0}


def _record(outcome: str, operation: str) -> None:
    _counts[outcome] += 1
    sora_telemetry_tasks_total.labels(outcome=outcome, operation=operation).inc()


def stats() -> dict:
    """The four outcomes, as counted in this process.

    `dropped` is not decoration: it increments whenever the bound above turns a
    submission away, which a 500-sample request reaches on arrival. A counter
    that cannot move is worse than no counter, so this one has a reachable path
    and a test that reaches it.

    `/metrics` is the interface; this exists because a test asserting on a
    global Prometheus registry is testing the registry.
    """
    with _lock:
        return dict(_counts, in_flight=_in_flight)


#: Per operation: (when it last reported, how many it has swallowed since).
#: Keyed by operation for the same reason as the one in `app.mlflow_tracking`
#: -- a burst is always of one kind, and a new kind must not wait behind it.
_failure_reports: dict = {}


def _report_failure(name: str, exc: BaseException) -> None:
    """Aggregate rather than narrate.

    This fires only for an exception that *escaped* the work function. The
    MLflow work catches its own and reports it through
    `app.mlflow_tracking._log_mlflow_failure`, which has the operation names
    and the credential sanitizer; this is the backstop for everything else, so
    it names the type only -- an escaped exception's message has not been
    through any sanitizer.
    """
    now = time.monotonic()
    with _lock:
        last, suppressed = _failure_reports.get(name, (None, 0))
        if last is not None and now - last < _FAILURE_LOG_INTERVAL:
            _failure_reports[name] = (last, suppressed + 1)
            return
        _failure_reports[name] = (now, 0)

    if suppressed:
        logger.warning(
            "telemetry task %s raised %s (and %d more in the last %.0fs)",
            name, type(exc).__name__, suppressed, _FAILURE_LOG_INTERVAL,
        )
    else:
        logger.warning("telemetry task %s raised %s", name, type(exc).__name__)


def _run(name: str, fn: Callable[..., Any], args: tuple, kwargs: dict) -> None:
    global _in_flight

    outcome = "failed"
    try:
        # The work functions catch their own MLflow error -- that is where the
        # operation name and the sanitizer live -- and say so by returning
        # False. Without reading that, `failed` could only ever count escapes,
        # which is to say almost never: a counter that cannot move.
        outcome = "failed" if fn(*args, **kwargs) is False else "succeeded"
    except BaseException as exc:  # noqa: BLE001 - a telemetry task must not escape
        # Nothing is retried. A tracking server that refused this event will
        # refuse the retry too, and the request it belonged to is long gone.
        _report_failure(name, exc)
        outcome = "failed"
    finally:
        # Recorded *before* the in-flight count drops, and under one lock. The
        # other order leaves a window where `drain()` reports nothing
        # outstanding while the outcome has not been counted yet -- a caller
        # that drains and then reads `stats()` would see the previous number,
        # sometimes.
        with _lock:
            _record(outcome, name)
            _in_flight -= 1


def submit(name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> bool:
    """Run `fn(*args, **kwargs)` off the caller's thread. Never raises.

    Returns whether it was scheduled, so a caller *can* tell -- but no caller
    checks, because there is nothing useful to do about a dropped metric and
    pretending otherwise would put branching for telemetry into the request
    path this exists to clear.

    Pass values, not objects with a lifecycle. Anything holding a request, a
    database session or an open connection outlives its scope here and will be
    touched after the response has been sent; snapshot what you need at the
    call site.
    """
    global _executor, _in_flight

    with _lock:
        if _shutting_down or _in_flight >= _MAX_IN_FLIGHT:
            _record("dropped", name)
            return False
        if _executor is None:
            # Built on first use, never at import: this module is imported by
            # the application at startup and starting threads there is a cost
            # paid by every process that merely imports it, tests included.
            _executor = ThreadPoolExecutor(
                max_workers=_WORKERS, thread_name_prefix="sora-telemetry")
        _in_flight += 1
        _record("scheduled", name)
        executor = _executor

    try:
        executor.submit(_run, name, fn, args, kwargs)
    except RuntimeError:
        # The pool was shut down between the check and the submit.
        with _lock:
            _in_flight -= 1
            _record("dropped", name)
        return False
    return True


def drain(timeout: float | None = None) -> int:
    """Wait for outstanding tasks. Returns how many were still in flight.

    Used by shutdown and by tests. A non-zero return is the honest answer that
    telemetry was lost, and it is reported rather than swallowed.
    """
    deadline = time.monotonic() + (timeout if timeout is not None else _SHUTDOWN_GRACE_SECONDS)
    while time.monotonic() < deadline:
        with _lock:
            if _in_flight == 0:
                return 0
        time.sleep(0.01)
    with _lock:
        return _in_flight


def shutdown() -> None:
    """Stop accepting work, wait briefly, and say what was lost."""
    global _shutting_down, _executor

    with _lock:
        _shutting_down = True
        executor = _executor

    lost = drain(_SHUTDOWN_GRACE_SECONDS)
    if lost:
        logger.warning(
            "MLflow telemetry: %d task(s) dropped at shutdown after %.1fs "
            "(best effort by design, not a durable queue)",
            lost, _SHUTDOWN_GRACE_SECONDS,
        )
    if executor is not None:
        executor.shutdown(wait=False)


atexit.register(shutdown)
