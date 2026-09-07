"""A metric nobody writes is not observability (#266).

Four `sora_*` metrics were declared and never set. Two of them had **alerts**
configured against them:

    increase(sora_drift_detected_total[5m]) > 0     grafana/.../alerts.yml:19
    sora_model_auc < 0.85                           grafana/.../alerts.yml:96

An expression that matches no series never fires. Those were not weak alerts;
they were the appearance of coverage over a model-quality and a drift signal,
and nothing would have told anyone.

The decision taken per metric: give `sora_drift_detected_total` and the refresh
counter a writer, and delete the two model-quality gauges -- there is no single
honest source for the AUC of the serving model while #232 is open, and putting
0.905 on a dashboard as fact is what that issue exists to prevent.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

#: Metrics with no writer in `app/`, each with the reason it is allowed to have
#: none. An allowance is a recorded decision; an empty list would be a rule
#: nobody could keep.
ALLOWED_WITHOUT_A_WRITER = {
    # Set in `app/prom_metrics.py` itself, at import, from constants.
    "sora_app_info": "set at import in prom_metrics.py",
}


def _defined_metrics() -> dict[str, str]:
    tree = ast.parse((ROOT / "app" / "prom_metrics.py").read_text())
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if getattr(node.value.func, "id", None) in {"Counter", "Gauge", "Histogram", "Info"}:
                var = getattr(node.targets[0], "id", None)
                if var and node.value.args:
                    out[var] = node.value.args[0].value
    return out


def _writers() -> dict[str, set[str]]:
    """Files that call `.inc()`/`.set()`/`.observe()` on each metric.

    Import aliases are resolved: `sora_drift_detected as sora_drift_detected_total`
    is how an earlier version of this scan reported a written metric as unwritten.
    """
    defs = _defined_metrics()
    writers: dict[str, set[str]] = {metric: set() for metric in defs.values()}
    for path in list((ROOT / "app").rglob("*.py")) + [ROOT / "run_scheduler.py"]:
        if not path.exists() or path.name == "prom_metrics.py":
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
                if local in alias:
                    writers[defs[alias[local]]].add(str(path.relative_to(ROOT)))
    return writers


# --- 1: the guard --------------------------------------------------------


def test_every_declared_metric_is_written_somewhere():
    """The rule, with its exceptions named rather than implied."""
    writers = _writers()
    assert len(writers) >= 20, f"the scan found only {len(writers)} metrics; it broke"

    unwritten = sorted(m for m, files in writers.items() if not files)
    unexplained = [m for m in unwritten if m not in ALLOWED_WITHOUT_A_WRITER]

    assert unexplained == [], (
        "declared and never set, so structurally absent from every scrape: "
        f"{unexplained}"
    )


def test_the_writer_scan_can_actually_find_nothing():
    """Negative control. An empty offender list is also what a broken scan gives."""
    writers = _writers()

    assert writers.get("sora_predictions_total"), "the scan sees no writer for a metric that has one"
    assert "sora_model_auc" not in writers, "the deleted gauge is still declared"


# --- 7: the deleted pair is gone everywhere ------------------------------


@pytest.mark.parametrize("metric", ["sora_model_auc", "sora_model_accuracy"])
def test_the_deleted_gauges_are_gone_from_code_dashboards_and_alerts(metric):
    """Deleting the definition and leaving a panel behind swaps one silent
    failure for another: a dashboard that reads "No data" forever."""
    assert metric not in _defined_metrics().values()

    dashboards = list((ROOT / "grafana").rglob("*.json"))
    assert dashboards, "no dashboards found; the scan proves nothing"
    for path in dashboards:
        assert metric not in path.read_text(), f"{metric} still queried in {path.name}"

    alerts = (ROOT / "grafana" / "provisioning" / "alerting" / "alerts.yml").read_text()
    assert metric not in alerts, f"{metric} still has an alert"

    for module in (ROOT / "app").rglob("*.py"):
        tree = ast.parse(module.read_text())
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        assert metric not in names, f"{metric} still imported or used in {module.name}"


def test_every_alert_expression_names_a_metric_that_exists():
    """The check the AUC alert would have failed.

    Only names declared in `prom_metrics.py` are required to exist; `up`,
    `http_requests_total` and the histogram suffixes come from elsewhere.
    """
    declared = set(_defined_metrics().values())
    alerts = yaml.safe_load(
        (ROOT / "grafana" / "provisioning" / "alerting" / "alerts.yml").read_text())

    exprs = []
    def walk(node):
        if isinstance(node, dict):
            if "expr" in node and isinstance(node["expr"], str):
                exprs.append(node["expr"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(alerts)
    assert exprs, "no alert expressions found; the scan proves nothing"

    import re

    missing = []
    for expr in exprs:
        for name in re.findall(r"\bsora_[a-z0-9_]+", expr):
            base = name
            for suffix in ("_bucket", "_sum", "_count"):
                if base.endswith(suffix):
                    base = base[: -len(suffix)]
            if base not in declared:
                missing.append((name, expr))

    assert missing == [], f"alerts on metrics that do not exist: {missing}"


# --- 2 to 6: the counters move only when the event happened ---------------


def _counter_value(counter, **labels) -> float:
    from prometheus_client import REGISTRY

    return REGISTRY.get_sample_value(counter, labels or None) or 0.0


@pytest.fixture()
def loop(monkeypatch):
    """`closed_loop_retrain` with its collaborators replaced, so the drift
    verdict is the only thing under test.

    The lock and the retrain are stubbed; the drift result is not -- it is the
    input whose three states this is about.
    """
    import types

    import app.scheduler as scheduler

    class _Lock:
        def __init__(self, **kw):
            pass

        @staticmethod
        def acquire():
            return True

        @staticmethod
        def release():
            return None

    monkeypatch.setitem(
        __import__("sys").modules, "app.locks", types.SimpleNamespace(RedisLock=_Lock))

    retrains = []

    def _no_retrain(**kwargs):
        retrains.append(kwargs)
        return {"status": "ok", "metrics": {}}

    import app.api.retrain as retrain_module

    monkeypatch.setattr(retrain_module, "_do_retrain", _no_retrain, raising=False)
    return types.SimpleNamespace(module=scheduler, retrains=retrains)


def _run_loop_with(monkeypatch, loop, status, drift_detected):
    import types

    import app.api.drift as drift_module

    verdict = types.SimpleNamespace(
        status=status, drift_detected=drift_detected, reason_code="probe")
    monkeypatch.setattr(drift_module, "compute_drift", lambda window=50: verdict)

    before = _counter_value("sora_drift_detected_total")
    result = loop.module.closed_loop_retrain(trigger_source="test")
    after = _counter_value("sora_drift_detected_total")
    return result, after - before


def test_measured_drift_increments_the_counter_once(monkeypatch, loop):
    _, delta = _run_loop_with(monkeypatch, loop, "ok", True)

    assert delta == 1.0


def test_no_drift_does_not_increment(monkeypatch, loop):
    result, delta = _run_loop_with(monkeypatch, loop, "ok", False)

    assert delta == 0.0
    assert result["drift_detected"] is False


def test_an_unavailable_check_does_not_increment(monkeypatch, loop):
    """A check that could not run is not an event. Counting it here would put a
    broken KS test on the drift alert as though the model had moved."""
    result, delta = _run_loop_with(monkeypatch, loop, "unavailable", None)

    assert delta == 0.0
    assert result["drift_detected"] is None
    assert result["reason"] == "drift_check_unavailable"


def test_reading_the_drift_endpoint_does_not_increment(monkeypatch):
    """`compute_drift` is also called by a read-only path. Counting there would
    make looking at the drift page indistinguishable from drift occurring."""
    import app.api.drift as drift_module

    before = _counter_value("sora_drift_detected_total")
    try:
        drift_module.compute_drift(window=50)
    except Exception:
        pass  # no data locally; what matters is that nothing counted
    after = _counter_value("sora_drift_detected_total")

    assert after == before


@pytest.mark.parametrize("outcome_status, expected", [
    ("success", "success"),
    ("partial", "failed"),
    ("error", "failed"),
])
def test_the_refresh_records_the_outcome_it_reported(monkeypatch, outcome_status, expected):
    """Not "it did not raise". `refresh_live_data` reports its own partial and
    error states, and collapsing those into success is how a broken source
    reads as healthy."""
    import types

    import app.scheduler as scheduler

    class _Lock:
        def __init__(self, **kw):
            pass

        @staticmethod
        def acquire():
            return True

        @staticmethod
        def release():
            return None

    class _Session:
        def add(self, *a):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    import sys

    monkeypatch.setitem(sys.modules, "app.locks", types.SimpleNamespace(RedisLock=_Lock))
    import app.database as database

    monkeypatch.setattr(database, "SessionLocal", lambda: _Session())
    import app.external_data as external

    monkeypatch.setattr(external, "refresh_live_data",
                        lambda trigger_source=None: {"status": outcome_status})

    labels = {"source": scheduler.EXTERNAL_REFRESH_SOURCE, "outcome": expected}
    before = _counter_value("sora_external_refresh_total", **labels)
    scheduler.scheduled_refresh_external_data()
    after = _counter_value("sora_external_refresh_total", **labels)

    assert after - before == 1.0


def test_a_skipped_refresh_is_counted_as_skipped(monkeypatch):
    """A refresh that keeps being turned away looks exactly like one that keeps
    succeeding, if only successes are counted."""
    import sys
    import types

    import app.scheduler as scheduler

    class _Lock:
        def __init__(self, **kw):
            pass

        @staticmethod
        def acquire():
            return False

        @staticmethod
        def release():
            return None

    monkeypatch.setitem(sys.modules, "app.locks", types.SimpleNamespace(RedisLock=_Lock))

    labels = {"source": scheduler.EXTERNAL_REFRESH_SOURCE, "outcome": "skipped"}
    before = _counter_value("sora_external_refresh_total", **labels)
    result = scheduler.scheduled_refresh_external_data()
    after = _counter_value("sora_external_refresh_total", **labels)

    assert result["status"] == "skipped"
    assert after - before == 1.0
