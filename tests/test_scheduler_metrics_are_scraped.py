"""The scheduler's metrics have to leave the scheduler (#267).

Eleven `sora_*` metrics are written **only** in the scheduler container --
`sora_retrain_total`, `sora_full_pipeline_total`, the four forecast gauges and
the five environmental ones. `run_scheduler.py` served no HTTP and
`infra/prometheus.yml` named one target, `backend:8000`, so each of them was
set into the memory of a process nobody asked and lost on the next restart.

`sora_retrain_total{status}` is what `CLAUDE.md` calls a key metric. It has
never reached Prometheus.
"""
from __future__ import annotations

import ast
import os
import socket
import subprocess
import sys
import textwrap
import time
import urllib.request
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

#: Metrics whose only writer is a module that runs in the scheduler container.
#: Derived below rather than trusted, so the list cannot quietly go stale.
SCHEDULER_MODULES = ("app/scheduler.py", "app/services/environmental/")


def _writers_by_metric() -> dict[str, set[str]]:
    """Which files write each metric, following `import X as Y`.

    The aliasing matters: `sora_drift_detected as sora_drift_detected_total` is
    how the first version of this scan reported a metric as unwritten for the
    wrong reason.
    """
    defs = {}
    for node in ast.walk(ast.parse((ROOT / "app" / "prom_metrics.py").read_text())):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if getattr(node.value.func, "id", None) in {"Counter", "Gauge", "Histogram"}:
                name = getattr(node.targets[0], "id", None)
                if name and node.value.args:
                    defs[name] = node.value.args[0].value

    writers: dict[str, set[str]] = {v: set() for v in defs.values()}
    for path in list((ROOT / "app").rglob("*.py")) + [ROOT / "run_scheduler.py"]:
        if not path.exists():
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        alias = {v: v for v in defs}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and "prom_metrics" in (node.module or ""):
                for a in node.names:
                    if a.name in defs:
                        alias[a.asname or a.name] = a.name
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) in {
                "set", "inc", "observe", "dec"
            }:
                base = node.func.value
                while isinstance(base, ast.Call):
                    base = base.func
                while isinstance(base, ast.Attribute):
                    base = base.value
                local = getattr(base, "id", None)
                if local in alias and path.name != "prom_metrics.py":
                    writers[defs[alias[local]]].add(str(path.relative_to(ROOT)))
    return writers


def test_metrics_written_only_by_the_scheduler_have_a_scrape_target():
    """The measurement that made this issue, kept as the assertion.

    If it ever finds nothing, that is not a pass -- it means the derivation
    broke -- so the count is asserted too.
    """
    writers = _writers_by_metric()
    scheduler_only = sorted(
        metric
        for metric, files in writers.items()
        if files and all(f.startswith(SCHEDULER_MODULES) for f in files)
    )

    assert len(scheduler_only) >= 10, (
        f"expected the scheduler-only set to stay substantial, found {scheduler_only}"
    )
    assert "sora_retrain_total" in scheduler_only, scheduler_only

    config = yaml.safe_load((ROOT / "infra" / "prometheus.yml").read_text())
    targets = [t for c in config["scrape_configs"] for t in c["static_configs"][0]["targets"]]

    assert any(t.startswith("scheduler:") for t in targets), (
        f"{len(scheduler_only)} metrics are written only in the scheduler and "
        f"nothing scrapes it. Targets: {targets}"
    )


def test_the_scheduler_has_a_job_name_of_its_own():
    """Both processes publish metrics of the same names. A single job with two
    targets makes a query unable to say which it means."""
    config = yaml.safe_load((ROOT / "infra" / "prometheus.yml").read_text())
    by_job = {c["job_name"]: c["static_configs"][0]["targets"] for c in config["scrape_configs"]}

    assert "sora-scheduler" in by_job, list(by_job)
    assert by_job["sora-scheduler"] == ["scheduler:9000"], by_job["sora-scheduler"]
    assert not any("scheduler" in t for t in by_job.get("sora-app", [])), by_job


def test_the_port_the_scheduler_serves_is_the_port_prometheus_scrapes():
    """Two files, one number. Written apart, they drift."""
    config = yaml.safe_load((ROOT / "infra" / "prometheus.yml").read_text())
    target = [
        t for c in config["scrape_configs"]
        for t in c["static_configs"][0]["targets"] if t.startswith("scheduler:")
    ][0]
    scraped_port = int(target.split(":")[1])

    tree = ast.parse((ROOT / "app" / "scheduler_metrics.py").read_text())
    default = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "DEFAULT_PORT":
            default = node.value.value
    assert default is not None, "DEFAULT_PORT is no longer a literal"
    assert default == scraped_port, (
        f"the scheduler serves :{default} and Prometheus scrapes :{scraped_port}"
    )


def test_run_scheduler_actually_calls_it():
    """The module can be perfect and never invoked.

    Asserted on the call site, because the two tests below exercise
    `app.scheduler_metrics` directly and would pass with `run_scheduler.py`
    never touching it.
    """
    tree = ast.parse((ROOT / "run_scheduler.py").read_text())

    imported = any(
        isinstance(n, ast.ImportFrom) and n.module == "app.scheduler_metrics"
        for n in ast.walk(tree)
    )
    called = any(
        isinstance(n, ast.Call) and getattr(n.func, "id", None) == "start_metrics_server"
        for n in ast.walk(tree)
    )

    assert imported, "run_scheduler.py does not import app.scheduler_metrics"
    assert called, "run_scheduler.py never calls start_metrics_server()"


def test_the_scheduler_is_not_published_to_the_host():
    """Inside the compose network only. A metrics endpoint on the public
    interface is an information disclosure nobody asked for."""
    compose = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text())
    scheduler = compose["services"]["scheduler"]

    assert "ports" not in scheduler, scheduler.get("ports")


def test_the_scheduler_refuses_to_serve_under_a_multiprocess_directory(monkeypatch, tmp_path):
    """It is one process. With the variable set, `start_http_server` would
    publish the memory-mapped files instead of this process's counters -- and
    sharing the backend's directory would mix two applications' lifetimes."""
    sys.path.insert(0, str(ROOT))
    import importlib

    # `app.scheduler_metrics`, not `run_scheduler`: importing the latter pulls
    # in APScheduler and the whole application to test a socket and a registry.
    module = importlib.import_module("app.scheduler_metrics")

    # Asserted on the *reason*, not on the return value, and this took two
    # tries to get right.
    #
    # `is False` alone passed with the guard deleted. Then `is False` plus a
    # free port passed too. `conftest.py` blocks the network for the whole
    # suite -- including `getaddrinfo`, which `start_http_server` calls -- so
    # inside a test the bind cannot succeed whatever the code does. The setup
    # was supplying the condition under test, and the check could not fail.
    #
    # What must hold is that the server is never *attempted*. That is
    # observable without a socket.
    import prometheus_client

    attempts = []
    monkeypatch.setattr(prometheus_client, "start_http_server",
                        lambda *a, **k: attempts.append(a))
    monkeypatch.setenv("SORA_SCHEDULER_METRICS_PORT", str(_free_port()))
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))

    assert module.start_metrics_server() is False
    assert attempts == [], "the server was started despite the multiprocess directory"


def test_without_the_multiprocess_directory_it_does_start_the_server(monkeypatch):
    """The other half, or the test above is satisfied by a function that never
    starts anything at all."""
    sys.path.insert(0, str(ROOT))
    import importlib

    import prometheus_client

    module = importlib.import_module("app.scheduler_metrics")
    port = _free_port()
    attempts = []
    monkeypatch.setattr(prometheus_client, "start_http_server",
                        lambda *a, **k: attempts.append(a))
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    monkeypatch.setenv("SORA_SCHEDULER_METRICS_PORT", str(port))

    assert module.start_metrics_server() is True
    assert attempts == [(port,)], attempts


def test_the_metrics_server_actually_serves_the_counters():
    """Run it. A port that binds and answers nothing is the failure this exists
    to rule out, and it looks identical from the outside."""
    port = _free_port()
    program = f"""
import os, sys, time
sys.path.insert(0, {str(ROOT)!r})
os.environ["SORA_SCHEDULER_METRICS_PORT"] = "{port}"
os.environ.pop("PROMETHEUS_MULTIPROC_DIR", None)
from app.scheduler_metrics import start_metrics_server
from app.prom_metrics import sora_retrain_total
assert start_metrics_server() is True
sora_retrain_total.labels(status="success").inc()
print("SERVING", flush=True)
time.sleep(30)
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(program)],
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "SORA_OFFLINE": "1", "RUN_SCHEDULER": "false",
             "DATABASE_URL": "sqlite:///./scheduler_metrics_probe.db",
             "REDIS_URL": "redis://127.0.0.1:6399/0",
             "SECRET_KEY": "test", "SORA_ADMIN_TOKEN": "test"},
    )
    try:
        deadline = time.monotonic() + 60
        body = ""
        while time.monotonic() < deadline:
            try:
                body = urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/metrics", timeout=5).read().decode()
                break
            except Exception:
                if proc.poll() is not None:
                    out, err = proc.communicate()
                    pytest.fail(f"the scheduler exited: {out}\n{err[-2000:]}")
                time.sleep(0.5)
        assert body, "the metrics endpoint never answered"
        assert 'sora_retrain_total{status="success"} 1.0' in body, [
            l for l in body.splitlines() if l.startswith("sora_retrain_total")
        ]
    finally:
        proc.kill()
        proc.wait(timeout=10)
        (ROOT / "scheduler_metrics_probe.db").unlink(missing_ok=True)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
