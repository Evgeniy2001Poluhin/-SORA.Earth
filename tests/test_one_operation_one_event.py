"""One user operation is one telemetry event (#258).

`calculate_esg` logged an evaluation itself, and it is called in loops. Measured
before the change, by counting submissions at the telemetry boundary through the
real endpoints:

| operation | MLflow events, before |
|---|---|
| `calculate_esg` (one call) | 1 |
| `POST /evaluate` | 2 (the same evaluation twice) |
| `POST /what-if` | 5 (baseline plus four variants) |
| `POST /evaluate/ranking` | 27 (one per country) |
| `POST /evaluate/monte-carlo` `n=50` | 50 |
| the same on its default `n=500` | 500 |

444 of those reached the production experiment on 2026-09-06 from two probe
requests, which is how the scale stopped being theoretical.

A scoring function does not keep a journal. `calculate_esg` is pure now, and
each sweep emits one aggregate instead -- the thing a reader of the experiment
wanted anyway.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app import mlflow_tracking

ROOT = Path(__file__).resolve().parents[1]

#: Everything that reaches MLflow. Named here rather than per test so a new
#: telemetry function cannot be added and quietly escape the loop guard below.
TELEMETRY = {"log_evaluation", "log_prediction", "log_simulation", "log_drift_event",
             "log_model_registry"}


@pytest.fixture()
def events(monkeypatch):
    """Count telemetry submissions, without running any of them."""
    recorded: list[tuple] = []

    class _Fake:
        @staticmethod
        def submit(name, fn, *args, **kwargs):
            recorded.append((name, args, kwargs))
            return True

    monkeypatch.setattr(mlflow_tracking, "telemetry", _Fake())
    monkeypatch.setattr(mlflow_tracking, "_OFFLINE", False)
    return recorded


def _names(recorded):
    return [name for name, _, _ in recorded]


def _metrics(recorded, index=0):
    return recorded[index][2]["metrics"]


# --- 1 and the structural guard --------------------------------------------


def test_calculate_esg_logs_nothing(events):
    """Check 1: a scoring function is a scoring function."""
    from app.main import Project, calculate_esg

    calculate_esg(
        Project(name="P", budget=100000, co2_reduction=150,
                social_impact=7, duration_months=24),
        "Europe",
    )

    assert events == [], f"calculate_esg emitted telemetry: {_names(events)}"


def test_calculate_esg_neither_calls_nor_imports_telemetry():
    """The architectural guard, checked statically.

    A runtime count says "it did not log this time". This says it cannot,
    which is the property worth keeping: the function is called from seven
    places, three of them loops, and the next caller will not check.
    """
    tree = ast.parse((ROOT / "app" / "main.py").read_text())
    fn = next(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "calculate_esg"
    )

    called, imported = [], []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in TELEMETRY:
                called.append(f"{name} at line {node.lineno}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "mlflow" in alias.name or "telemetry" in alias.name:
                    imported.append(f"{alias.name} at line {node.lineno}")
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if "mlflow" in module or "telemetry" in module:
                imported.append(f"{module} at line {node.lineno}")

    assert called == [], f"calculate_esg calls telemetry: {called}"
    assert imported == [], f"calculate_esg imports telemetry: {imported}"


# --- 9: no loop may log per iteration --------------------------------------


@pytest.mark.parametrize("module", ["app/api/evaluate.py", "app/main.py"])
def test_no_telemetry_call_sits_inside_a_loop(module):
    """Check 9, enumerated structurally rather than by inspecting the files I
    happened to remember. Comprehensions count: they are loops that do not look
    like one."""
    tree = ast.parse((ROOT / module).read_text())

    spans = [
        (node.lineno, node.end_lineno)
        for node in ast.walk(tree)
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While,
                             ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp))
    ]

    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in TELEMETRY and any(lo <= node.lineno <= hi for lo, hi in spans):
                offenders.append(f"{name} at {module}:{node.lineno}")

    assert offenders == [], f"telemetry inside a loop: {offenders}"


def test_the_loop_scan_can_actually_find_something():
    """Negative control: an empty offender list is what a broken scan produces too."""
    source = "for x in y:\n    log_evaluation(1, 2, 3)\n"
    tree = ast.parse(source)
    spans = [(n.lineno, n.end_lineno) for n in ast.walk(tree) if isinstance(n, ast.For)]
    found = [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and (getattr(n.func, "id", None) in TELEMETRY)
        and any(lo <= n.lineno <= hi for lo, hi in spans)
    ]

    assert found == [2]


# --- 2 to 7: one event per user operation, through the real endpoints -------


@pytest.fixture()
def api(client):
    """The application's own TestClient, from conftest."""
    return client


#: `/api/v1/evaluate` caches on the request body for ten minutes
#: (`app/api/evaluate.py:115`), so two tests posting the same payload leave the
#: second served from cache with no telemetry at all -- it fails, and for a
#: reason that has nothing to do with its subject. Found the hard way. Each
#: test gets its own body.
_COUNTER = iter(range(1, 10_000))


def _project(**overrides):
    body = {"name": f"P{next(_COUNTER)}", "budget": 100000, "co2_reduction": 150,
            "social_impact": 7, "duration_months": 24}
    body.update(overrides)
    return body


PROJECT = _project()
MC = {"project_name": "P", "region": "Sweden", "budget_usd": 100000,
      "co2_reduction_tons_per_year": 150, "social_impact_score": 7,
      "project_duration_months": 24, "noise": 0.15}


def test_evaluate_emits_exactly_one_event(api, events):
    """Check 2. It used to emit three, two of them the same evaluation."""
    r = api.post("/api/v1/evaluate", json=_project())

    assert r.status_code == 200, r.text
    assert _names(events) == ["log_evaluation"], _names(events)


def test_evaluate_still_writes_its_prediction_log_row(api, events):
    """`app.main.log_prediction` is a database write, not telemetry.

    It shares a name with `app.mlflow_tracking.log_prediction` and does
    something else: a row in `predictions_log`. I removed the call while
    reading it as a duplicate MLflow event. Mutation testing caught it -- every
    assertion stayed green when the call was put back, which is what a test
    that does not cover something looks like. This is that test.
    """
    from app.database import PredictionLog
    from app.main import get_db_sync

    db = get_db_sync()
    try:
        before = db.query(PredictionLog).filter(PredictionLog.endpoint == "evaluate").count()
    finally:
        db.close()

    r = api.post("/api/v1/evaluate", json=_project())
    assert r.status_code == 200, r.text

    db = get_db_sync()
    try:
        after = db.query(PredictionLog).filter(PredictionLog.endpoint == "evaluate").count()
    finally:
        db.close()

    assert after == before + 1, "the /evaluate prediction-log row was not written"


@pytest.mark.parametrize("n, samples", [(50, 50), (500, 500)])
def test_monte_carlo_emits_one_event_whatever_the_sample_count(api, events, n, samples):
    """Check 3. `n=50` ran 50 samples and logged 50; `n=500`, five hundred."""
    r = api.post("/api/v1/evaluate/monte-carlo", json={**MC, "n": n})

    assert r.status_code == 200, r.text
    assert _names(events) == ["log_simulation"], f"{len(events)} events for {samples} samples"
    metrics = _metrics(events)
    assert metrics["requested"] == samples
    assert metrics["succeeded"] == samples


def test_a_partly_failed_sweep_reports_requested_as_succeeded_plus_failed(api, events, monkeypatch):
    """Check 4."""
    import app.api.evaluate as ev

    calls = {"n": 0}
    real = ev._simulate_once

    def flaky(req, region_name, jitter):
        calls["n"] += 1
        if calls["n"] % 5 == 0:
            raise ValueError("one bad sample")
        return real(req, region_name, jitter)

    monkeypatch.setattr(ev, "_simulate_once", flaky)
    r = api.post("/api/v1/evaluate/monte-carlo", json={**MC, "n": 50})

    assert r.status_code == 200, r.text
    assert _names(events) == ["log_simulation"]
    m = _metrics(events)
    assert m["requested"] == m["succeeded"] + m["failed"] == 50
    assert m["failed"] == 10
    assert events[0][2]["tags"]["partial_sample"] == "true"


def test_a_completely_failed_sweep_emits_one_event_not_fifty(api, events, monkeypatch):
    """Check 5. Fifty raised samples must not become fifty telemetry events.

    The issue allowed a counter instead of an event here; an event is used
    because the alternative leaves the experiment unable to tell "nobody asked"
    from "everything raised" -- the same shape as a failure that reads as calm.
    """
    import app.api.evaluate as ev

    monkeypatch.setattr(
        ev, "_simulate_once",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("always")),
    )
    r = api.post("/api/v1/evaluate/monte-carlo", json={**MC, "n": 50})

    assert r.status_code == 503, r.text
    assert _names(events) == ["log_simulation"], _names(events)
    m = _metrics(events)
    assert m["succeeded"] == 0
    assert m["failed"] == 50
    assert events[0][2]["tags"]["outcome"] == "no_successful_runs"


def test_what_if_emits_one_event_for_the_whole_sweep(api, events):
    """Check 6. Five calculations -- baseline and four variants -- one event."""
    r = api.post("/api/v1/what-if", json=_project())

    assert r.status_code == 200, r.text
    assert _names(events) == ["log_simulation"], _names(events)
    m = _metrics(events)
    assert m["variants"] == 4
    assert m["baseline_score"] > 0


def test_the_ranking_sweep_emits_one_event_for_twenty_seven_countries(api, events):
    """The third sweep, absent from the issue and found by enumerating callers."""
    r = api.post("/api/v1/evaluate/ranking", json=_project())

    assert r.status_code == 200, r.text
    assert _names(events) == ["log_simulation"], _names(events)
    assert _metrics(events)["countries"] == r.json()["count"] > 1


def test_the_prediction_endpoints_keep_their_own_telemetry(api, events):
    """Check 7. This change removes duplicates, not the prediction record."""
    r = api.post("/api/v1/predict", json=_project())

    assert r.status_code == 200, r.text
    assert _names(events) == ["log_prediction"], _names(events)


def test_an_mlflow_failure_does_not_change_the_response(api, monkeypatch):
    """Check 8, taken through the real dispatcher rather than the fake."""
    import app.mlflow_tracking as m

    monkeypatch.setattr(m, "_OFFLINE", False)
    monkeypatch.setattr(
        m, "_log_simulation_now",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("MLflow is down")),
    )

    r = api.post("/api/v1/evaluate/monte-carlo", json={**MC, "n": 50})

    assert r.status_code == 200, r.text
    assert r.json()["n"] == 50


def test_no_request_payload_reaches_an_aggregate(api, events):
    """An aggregate is a summary. The project name is the caller's text."""
    r = api.post("/api/v1/evaluate/monte-carlo",
                 json={**MC, "project_name": "SECRET-PROJECT-NAME", "n": 50})

    assert r.status_code == 200
    _, args, kwargs = events[0]
    rendered = repr(args) + repr(kwargs)
    assert "SECRET-PROJECT-NAME" not in rendered, rendered
