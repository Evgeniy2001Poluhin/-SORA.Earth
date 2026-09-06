"""Importing `app.mlflow_tracking` must not touch the network (issue 243).

`_ensure_experiment()` makes an HTTP call, and it used to run at module level.
MLflow's default `MLFLOW_HTTP_REQUEST_TIMEOUT` is 120 seconds, so with a
tracking server configured but unreachable, `import app.mlflow_tracking`
blocked -- and four modules import it at module level, so importing any of
*them* blocked as well, application startup included.

The `try` around it caught exceptions and could not catch slowness, which is
exactly why it looked safe for months. `SORA_OFFLINE=1` in the test environment
skipped the call, so CI never paid the cost either.

Measured 2026-09-05: `import mlflow` 1.1s; `import app.mlflow_tracking` over
30s and still running; with `SORA_OFFLINE=1`, 1.0s.
"""
from __future__ import annotations

import importlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.timeout(120)
def test_the_import_makes_no_http_call_even_with_a_tracking_uri_set():
    """Run in a subprocess with sockets instrumented and OFFLINE off.

    A subprocess, because the module may already be imported by another test
    and an import that has happened cannot be re-observed. And OFFLINE off,
    because that flag is what hid this: with it on the call never happens, and
    the test would pass without exercising anything.

    The instrumentation *records* connection attempts rather than raising them.
    An earlier version raised, and mutation testing showed it could not fail:
    the module-level `try/except Exception` swallowed the AssertionError and
    the import completed normally -- the same `except` that hid the real defect
    for months hid the test written to catch it. Evidence must not travel by a
    route the subject is allowed to discard.
    """
    program = """
import socket, sys

ATTEMPTS = []

_real_socket = socket.socket
_real_create_connection = socket.create_connection


class Recording(_real_socket):
    def connect(self, address, *a, **k):
        ATTEMPTS.append(("connect", address))
        raise OSError("blocked by the test")

    def connect_ex(self, address, *a, **k):
        ATTEMPTS.append(("connect_ex", address))
        return 111


def recording_create_connection(address, *a, **k):
    ATTEMPTS.append(("create_connection", address))
    raise OSError("blocked by the test")


socket.socket = Recording
socket.create_connection = recording_create_connection

import app.mlflow_tracking  # noqa: F401

# Printed unconditionally, so a swallowed exception cannot be mistaken for a
# clean import.
print("ATTEMPTS=" + repr(ATTEMPTS))
"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": "/tmp",
            # Deliberately NOT offline, and pointed at an address nothing serves.
            "SORA_OFFLINE": "0",
            "MLFLOW_TRACKING_URI": "http://127.0.0.1:59999",
        },
    )

    line = [l for l in result.stdout.splitlines() if l.startswith("ATTEMPTS=")]
    assert line, (
        "the subprocess did not finish the import.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr[-2000:]}"
    )
    attempts = line[0][len("ATTEMPTS="):]
    assert attempts == "[]", (
        f"importing app.mlflow_tracking opened connections: {attempts}"
    )


@pytest.mark.timeout(120)
def test_the_import_is_fast_with_an_unreachable_tracking_server():
    """The observable symptom, bounded.

    Not a substitute for the socket check above -- a slow import with no
    network would pass this and fail that -- but it is the thing an operator
    actually noticed, so it is asserted directly.
    """
    import time

    started = time.monotonic()
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import app.mlflow_tracking; print('ok')"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            # Below MLflow's own 120s request timeout on purpose: if the import
            # is waiting on the tracking server, this expires first and says so.
            timeout=40,
            env={
                "PATH": "/usr/bin:/bin:/usr/local/bin",
                "HOME": "/tmp",
                "SORA_OFFLINE": "0",
                "MLFLOW_TRACKING_URI": "http://127.0.0.1:59999",
            },
        )
    except subprocess.TimeoutExpired:
        raise AssertionError(
            "importing app.mlflow_tracking did not finish in 40s with an "
            "unreachable tracking server -- it is waiting on the network"
        )
    elapsed = time.monotonic() - started

    assert "ok" in result.stdout, result.stderr[-2000:]
    assert elapsed < 30, f"import took {elapsed:.1f}s with an unreachable tracking server"


def test_the_experiment_is_resolved_at_most_once():
    module = importlib.import_module("app.mlflow_tracking")
    calls = {"n": 0}

    def counting(api=None):
        calls["n"] += 1

    original_flag = module._experiment_ready
    original_fn = module._ensure_experiment
    original_offline = module._OFFLINE
    try:
        module._experiment_ready = False
        module._OFFLINE = False
        module._ensure_experiment = counting

        module._ensure_experiment_once()
        module._ensure_experiment_once()
        module._ensure_experiment_once()
    finally:
        module._ensure_experiment = original_fn
        module._experiment_ready = original_flag
        module._OFFLINE = original_offline

    assert calls["n"] == 1, "the network call must happen once per process, not per log"


def test_a_failing_resolution_does_not_raise_into_the_caller():
    """MLflow is best-effort. A tracking server being down must not fail a
    prediction, and the flag must stay false so a later call can retry."""
    module = importlib.import_module("app.mlflow_tracking")

    original_flag = module._experiment_ready
    original_fn = module._ensure_experiment
    original_offline = module._OFFLINE
    try:
        module._experiment_ready = False
        module._OFFLINE = False
        module._ensure_experiment = lambda api=None: (_ for _ in ()).throw(
            ConnectionError("down"))

        module._ensure_experiment_once()  # must not raise

        assert module._experiment_ready is False, "a failure must not be recorded as success"
    finally:
        module._ensure_experiment = original_fn
        module._experiment_ready = original_flag
        module._OFFLINE = original_offline


def test_the_docstring_names_every_module_level_importer():
    """The blast-radius claim is derived, not remembered.

    The first version of that docstring said "four modules"; AST said nine,
    and one of the five it omitted was `app/main.py` -- so the sentence
    understated the defect as transitive when startup blocked directly. A
    count written from recall is how that happens, so the count is checked.
    """
    import ast as ast_mod

    module_level = set()
    for path in sorted((ROOT / "app").rglob("*.py")):
        if path.name == "mlflow_tracking.py":
            continue
        try:
            tree = ast_mod.parse(path.read_text())
        except SyntaxError:
            continue
        for node in tree.body:  # module level only, not nested in functions
            for sub in ast_mod.walk(node):
                names = []
                if isinstance(sub, ast_mod.Import):
                    names = [a.name for a in sub.names]
                elif isinstance(sub, ast_mod.ImportFrom):
                    names = [sub.module or ""]
                if any("mlflow_tracking" in n for n in names):
                    module_level.add(str(path.relative_to(ROOT)))

    module = importlib.import_module("app.mlflow_tracking")
    doc = module._ensure_experiment_once.__doc__ or ""
    named = set(re.findall(r"`(app/[\w/]+\.py)`", doc))

    assert named == module_level, (
        "the docstring's list of module-level importers is stale.\n"
        f"  named but not importing:  {sorted(named - module_level)}\n"
        f"  importing but not named:  {sorted(module_level - named)}"
    )

    # And the prose count agrees with the list, so the two cannot drift apart.
    words = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    }
    stated = re.search(r"\b(" + "|".join(words) + r")\b modules import it", doc.lower())
    assert stated, "the docstring no longer states how many modules import it"
    assert words[stated.group(1)] == len(module_level), (
        f"the docstring says {stated.group(1)}, AST found {len(module_level)}"
    )


def test_application_startup_is_on_the_blocked_path():
    """`app/main.py` imports it at module level, so this is not transitive.

    Asserted separately from the list above because it is the fact that makes
    the issue a startup defect rather than a slow endpoint, and a list check
    would go green if `main.py` were simply dropped from both sides.
    """
    import ast as ast_mod

    tree = ast_mod.parse((ROOT / "app" / "main.py").read_text())
    at_module_level = any(
        (isinstance(sub, ast_mod.Import) and any("mlflow_tracking" in a.name for a in sub.names))
        or (isinstance(sub, ast_mod.ImportFrom) and "mlflow_tracking" in (sub.module or ""))
        for node in tree.body
        for sub in ast_mod.walk(node)
    )
    assert at_module_level, "app/main.py no longer imports mlflow_tracking at module level"


def test_the_experiment_lookup_is_bounded_and_the_bound_is_put_back(monkeypatch):
    """The lookup window is the whole reason the lazy init is safe.

    Without it the 247s wait measured against a dead port would simply move
    from startup into the first prediction. And the restore matters as much as
    the bound: leaving a 5s timeout installed would silently shorten model
    artefact uploads during retraining, which legitimately take longer.

    The starting environment is **set here** rather than read from whatever the
    process happens to hold. The first version read it, and mutation testing
    showed the restore could be deleted with the test still green: an earlier
    test in the same file had already been through the window, so "before" and
    "after" were both the bound's own values and agreed with each other. A
    check whose baseline is supplied by the thing it is checking cannot fail.
    One value distinctive, one deliberately absent, so both restore paths --
    put the old value back, and remove a key that was not there -- are covered.
    """
    module = importlib.import_module("app.mlflow_tracking")

    expected = {
        "MLFLOW_HTTP_REQUEST_TIMEOUT": "120",
        "MLFLOW_HTTP_REQUEST_MAX_RETRIES": None,
        "MLFLOW_HTTP_REQUEST_BACKOFF_FACTOR": "2",
    }
    assert set(expected) == set(module._EXPERIMENT_LOOKUP_ENV), (
        "the bound covers different variables than this test sets up"
    )
    for key, value in expected.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)

    seen = {}

    class _Api:
        def get_experiment_by_name(self, name):
            seen.update({k: os.environ.get(k) for k in expected})
            raise ConnectionError("down")

    monkeypatch.setattr(module, "_experiment_ready", False)
    monkeypatch.setattr(module, "_OFFLINE", False)
    module._ensure_experiment_once(_Api())

    assert seen == module._EXPERIMENT_LOOKUP_ENV, (
        f"the lookup ran without its bound installed: {seen}"
    )
    after = {k: os.environ.get(k) for k in expected}
    assert after == expected, f"the bound was left behind: {after} (was {expected})"


def test_a_concurrent_first_use_resolves_once():
    """"Once per process" has to hold across threads.

    FastAPI runs sync handlers in a threadpool, so the first use after startup
    is plausibly several requests at the same moment. Without the lock each of
    them would make its own lookup, which is the wait this change exists to
    remove, multiplied.
    """
    import threading

    module = importlib.import_module("app.mlflow_tracking")
    calls = []
    started = threading.Barrier(8)

    def slow(api=None):
        calls.append(1)
        time.sleep(0.05)

    original_flag = module._experiment_ready
    original_fn = module._ensure_experiment
    original_offline = module._OFFLINE
    try:
        module._experiment_ready = False
        module._OFFLINE = False
        module._ensure_experiment = slow

        def worker():
            started.wait(timeout=10)
            module._ensure_experiment_once()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
    finally:
        module._ensure_experiment = original_fn
        module._experiment_ready = original_flag
        module._OFFLINE = original_offline

    assert len(calls) == 1, f"{len(calls)} threads each made their own lookup"


@pytest.mark.timeout(120)
def test_the_lazy_resolution_itself_returns_promptly_against_a_dead_server():
    """The bound, end to end, on the call this change introduces.

    Scoped deliberately to `_ensure_experiment_once`. An earlier version of
    this test called `log_prediction` and failed at 90 seconds -- because
    `mlflow.start_run` makes its own unbounded request, so a logging call
    against a dead tracking server blocks for the full 247s. That is true
    before this change and after it, in every prediction rather than once per
    process, and it is filed separately rather than quietly folded in here.
    """
    program = """
import time
import app.mlflow_tracking as m

started = time.monotonic()
m._ensure_experiment_once()
print("ELAPSED=%.2f" % (time.monotonic() - started))
print("READY=%s" % m._experiment_ready)
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", program],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=90,
            env={
                "PATH": "/usr/bin:/bin:/usr/local/bin",
                "HOME": "/tmp",
                "SORA_OFFLINE": "0",
                "MLFLOW_TRACKING_URI": "http://127.0.0.1:59999",
            },
        )
    except subprocess.TimeoutExpired:
        raise AssertionError(
            "resolving the experiment did not return in 90s against a dead "
            "tracking server -- the lookup bound is not being applied"
        )
    line = [l for l in result.stdout.splitlines() if l.startswith("ELAPSED=")]
    assert line, f"the call did not return.\nstdout: {result.stdout}\nstderr: {result.stderr[-2000:]}"
    elapsed = float(line[0][len("ELAPSED="):])
    assert elapsed < 30, (
        f"resolving the experiment took {elapsed:.1f}s against a dead tracking "
        "server; the lookup bound is not being applied"
    )
    assert "READY=False" in result.stdout, (
        "a failed resolution must not be recorded as success"
    )
