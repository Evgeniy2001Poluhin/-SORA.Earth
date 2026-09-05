"""Every branch of GET /api/v1/model/drift, against the declared contract (#239).

This is the first migrated endpoint under docs/API_CONTRACT_ROADMAP.md, and the
point of migrating it is that a consumer can no longer read a missing key. So
the assertions here are about shape as much as values, and every branch is
exercised -- including the ones only reached when something is broken, which is
where the old response disagreed with itself.

The rule that matters most: a status other than "ok" must carry
`drift_detected: null`, never `false`. False asserts that drift was looked for
and not found, which on those branches nobody did.
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

drift_module = importlib.import_module("app.api.drift")

#: Every key a 200 must carry, whatever its status. The old endpoint's defect
#: was that this set changed per branch.
REQUIRED_KEYS = {"status", "drift_detected", "window", "observations", "features", "reason_code"}


@pytest.fixture(autouse=True)
def no_mlflow(monkeypatch):
    """Keep the MLflow hook out of the request path.

    The handler logs a drift event synchronously, inside the GET, whenever it
    finds drift -- and it does `from app.mlflow_tracking import log_drift_event`
    at that point, inside the request. **Importing that module blocks** with no
    tracking server reachable, so the request never returns; the two tests that
    reach the KS branch hung indefinitely before this fixture existed.

    A stub module is installed in `sys.modules` rather than an attribute being
    patched, because patching the attribute requires importing the module
    first, which is the thing that blocks.

    Stubbed here rather than fixed: a blocking import inside a read endpoint is
    a real defect, but it is not this contract, and #239 is deliberately one
    route wide. Filed as issue 243.
    """
    stub = types.ModuleType("app.mlflow_tracking")
    stub.log_drift_event = lambda *a, **k: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.mlflow_tracking", stub)


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(drift_module.router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    """Point the endpoint at a scratch baseline and log."""
    baseline = tmp_path / "projects.csv"
    log = tmp_path / "predictions_log.csv"
    monkeypatch.setattr(drift_module, "PROJ_CSV", str(baseline))
    monkeypatch.setattr(drift_module, "PRED_LOG", str(log))
    return baseline, log


def write_rows(path: Path, n: int, budget: int = 100, cycle: int = 0) -> None:
    """Write `n` rows.

    With `cycle`, values repeat over that many distinct levels, so two files
    written with the same `budget` and `cycle` hold the same empirical
    distribution however many rows each has. Without it the values climb, and
    two files of different lengths differ in range -- which is what made the
    first version of the "no drift" test assert against a KS result that had
    genuinely found something.
    """
    header = "budget,co2_reduction,social_impact,duration_months\n"
    step = (lambda i: i % cycle) if cycle else (lambda i: i)
    rows = "".join(f"{budget + step(i)},{50 + step(i)},{5},{12}\n" for i in range(n))
    path.write_text(header + rows, encoding="utf-8")


def test_no_prediction_log_is_a_domain_state_not_a_verdict(client, paths):
    baseline, log = paths
    write_rows(baseline, 40)
    assert not log.exists()

    r = client.get("/api/v1/model/drift")

    assert r.status_code == 200, "no data yet is an answer about the world, not a failure"
    body = r.json()
    assert set(body) == REQUIRED_KEYS
    assert body["status"] == "no_log"
    assert body["drift_detected"] is None, "false would claim drift was looked for"
    assert body["observations"] == 0
    assert body["features"] == {}
    assert body["reason_code"] == "prediction_log_absent"


def test_too_few_rows_is_a_domain_state_not_a_verdict(client, paths):
    baseline, log = paths
    write_rows(baseline, 40)
    write_rows(log, drift_module.MIN_WINDOW_ROWS - 1)

    r = client.get("/api/v1/model/drift")

    assert r.status_code == 200
    body = r.json()
    assert set(body) == REQUIRED_KEYS
    assert body["status"] == "insufficient_data"
    assert body["drift_detected"] is None
    assert body["observations"] == drift_module.MIN_WINDOW_ROWS - 1
    assert body["features"] == {}
    assert body["reason_code"] == "below_minimum_window"


def test_a_computed_verdict_carries_a_boolean_and_its_features(client, paths):
    baseline, log = paths
    write_rows(baseline, 60, budget=100)
    # Far from the baseline, so the KS test has something to find.
    write_rows(log, 40, budget=900_000)

    r = client.get("/api/v1/model/drift")

    assert r.status_code == 200
    body = r.json()
    assert set(body) == REQUIRED_KEYS
    assert body["status"] == "ok"
    assert isinstance(body["drift_detected"], bool), "a computed verdict is never null"
    assert body["observations"] == 40
    assert body["reason_code"] is None
    assert body["features"], "an 'ok' answer reports the features it tested"
    for name, stat in body["features"].items():
        assert set(stat) == {"ks_stat", "p_value", "drift"}, name


def test_identical_distributions_report_no_drift_rather_than_no_answer(client, paths):
    baseline, log = paths
    # Same ten levels in both, so the two empirical distributions match and the
    # KS test genuinely finds nothing -- rather than the two files merely
    # starting at the same number and covering different ranges.
    write_rows(baseline, 60, budget=100, cycle=10)
    write_rows(log, 40, budget=100, cycle=10)

    body = client.get("/api/v1/model/drift").json()

    assert body["status"] == "ok"
    assert body["drift_detected"] is False, "measured and not found IS false; only unmeasured is null"


def test_missing_scipy_is_a_deployment_fault_not_a_drift_answer(client, paths, monkeypatch):
    baseline, log = paths
    write_rows(baseline, 40)
    write_rows(log, 40)
    monkeypatch.setattr(drift_module, "HAS_SCIPY", False)

    r = client.get("/api/v1/model/drift")

    assert r.status_code == 503, "scipy==1.13.1 is a declared dependency; its absence is a fault"
    body = r.json()
    assert set(body) == REQUIRED_KEYS
    assert body["status"] == "unavailable"
    assert body["drift_detected"] is None
    assert body["reason_code"] == "scipy_missing"


def test_a_missing_baseline_is_a_fault_rather_than_a_traceback(client, paths):
    baseline, log = paths
    write_rows(log, 40)
    assert not baseline.exists()

    r = client.get("/api/v1/model/drift")

    assert r.status_code == 503, "this used to raise inside pandas and answer 500"
    assert r.json()["reason_code"] == "baseline_missing"


def test_the_window_is_echoed_on_every_branch(client, paths):
    baseline, log = paths
    write_rows(baseline, 40)

    body = client.get("/api/v1/model/drift?window=7").json()

    assert body["window"] == 7


@pytest.mark.parametrize("status_name", ["no_log", "insufficient_data", "unavailable"])
def test_no_branch_reports_a_falsy_verdict(client, paths, monkeypatch, status_name):
    """The single rule this migration exists for, asserted across every branch.

    `drift_detected: false` on a branch that measured nothing is what let "no
    prediction log" reach the screen as a negative drift verdict.
    """
    baseline, log = paths
    if status_name == "no_log":
        write_rows(baseline, 40)
    elif status_name == "insufficient_data":
        write_rows(baseline, 40)
        write_rows(log, 2)
    else:
        write_rows(baseline, 40)
        write_rows(log, 40)
        monkeypatch.setattr(drift_module, "HAS_SCIPY", False)

    body = client.get("/api/v1/model/drift").json()

    assert body["status"] == status_name
    assert body["drift_detected"] is None
    assert body["drift_detected"] is not False


def test_the_retired_field_name_is_gone(client, paths):
    """`drift` and `drift_detected` meant the same thing on different branches.

    The only consumer reads `features`, so removing `drift` breaks nothing --
    verified before the change by grepping web/src for every field of this
    response.
    """
    baseline, log = paths
    write_rows(baseline, 40)

    assert "drift" not in client.get("/api/v1/model/drift").json()


def test_openapi_declares_the_required_keys_for_every_branch(client):
    """The schema has to describe what the endpoint actually returns.

    Without this, `response_model` could be narrowed later and the published
    document would keep promising fields nobody sends.
    """
    schema = client.get("/openapi.json").json()
    op = schema["paths"]["/api/v1/model/drift"]["get"]

    assert "503" in op["responses"], "the fault branch must be declared, not implied"

    components = schema["components"]["schemas"]
    for model in ("ModelDriftMeasured", "ModelDriftNotMeasured", "ModelDriftUnavailable"):
        assert model in components, model
        props = set(components[model]["properties"])
        assert REQUIRED_KEYS <= props, f"{model} is missing {REQUIRED_KEYS - props}"


def test_openapi_pins_the_verdict_to_the_status(client):
    """`ok` promises a boolean; the other statuses promise null."""
    components = client.get("/openapi.json").json()["components"]["schemas"]

    measured = components["ModelDriftMeasured"]["properties"]["drift_detected"]
    assert measured.get("type") == "boolean"

    for model in ("ModelDriftNotMeasured", "ModelDriftUnavailable"):
        verdict = components[model]["properties"]["drift_detected"]
        assert verdict.get("type") == "null", f"{model} must promise null, not a boolean"
