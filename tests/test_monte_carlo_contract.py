"""Every branch of POST /api/v1/evaluate/monte-carlo (docs/API_CONTRACT_ROADMAP.md).

Two defects, both of the shape this roadmap is about.

"Every simulation raised" answered 200 with `{"error": "no successful runs"}`.
A caller reading `mean` got `undefined`, and `undefined` reads as zero on the
way to a chart. It is not a state of the world; it is the service computing
nothing.

And `n` counted *successful* runs while reading as the number requested. Ask
for 1000, have 950 raise, and the answer said `n: 50` -- indistinguishable from
a request for 50. A distribution built from a twentieth of the sample presented
itself as a distribution.
"""
from __future__ import annotations

import importlib
import sys
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Installed before importing the endpoint, not in a fixture: `app.api.evaluate`
# imports `app.mlflow_tracking` at module level, and importing *that* runs a
# network call with no timeout (issue 243). A fixture is too late -- the import
# has already blocked by the time one runs. This is why the first version of
# this file took four minutes for eight tests.
#
# `setdefault`, deliberately: in CI `conftest.py` imports `app.main` first, so
# the real module is already registered and this is a no-op. The stub is only
# used where nothing has imported it yet -- a targeted local run. That keeps
# the blast radius to the case that needs it rather than replacing a shared
# module for the whole session, which is what an earlier version of the
# clamping test did to `app.main` and what broke this file in CI.
class _MlflowStub(types.ModuleType):
    """Answers to any name with a no-op.

    Listing the functions each test file happens to need makes the stub
    order-dependent: whichever file installs it first wins, and the next one
    fails on a name the first did not think of. That happened between the
    simulate and Monte Carlo contract files -- `log_evaluation` was missing
    from the stub the other had already installed.
    """

    def __getattr__(self, _name):
        return lambda *a, **k: None


sys.modules.setdefault("app.mlflow_tracking", _MlflowStub("app.mlflow_tracking"))

evaluate_module = importlib.import_module("app.api.evaluate")

OK_KEYS = {
    "status", "requested", "n", "failed", "mean", "stdev", "min", "max",
    "p10", "p50", "p90", "histogram", "reason_code",
}

REQUEST = {
    "project_name": "P",
    "region": "Sweden",
    "budget_usd": 150000,
    "co2_reduction_tons_per_year": 120,
    "social_impact_score": 8,
    "project_duration_months": 24,
    "n": 60,
}


@pytest.fixture(autouse=True)
def seam(monkeypatch):
    """Replace the two `app.main` seams so no test imports the application.

    Before the seams existed, every request in this file imported `app.main`
    inside the handler -- eight targeted cases took four minutes and exercised
    the application's startup rather than this contract. `_simulate_once` and
    `_region_name` are the whole surface that needs replacing.

    The default is a deterministic score, so a test that cares only about the
    envelope does not have to say anything about the simulation.
    """
    monkeypatch.setattr(evaluate_module, "_region_name", lambda region: "Europe")
    monkeypatch.setattr(evaluate_module, "_simulate_once", lambda req, region, jitter: 61.5)


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(evaluate_module.router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


def always_raises(exc: Exception):
    def _fail(*_a, **_k):
        raise exc

    return _fail


def post(client: TestClient, **overrides):
    return client.post("/api/v1/evaluate/monte-carlo", json={**REQUEST, **overrides})


def test_a_computed_distribution_states_both_counts(client):
    r = post(client)

    assert r.status_code == 200
    body = r.json()
    assert set(body) == OK_KEYS
    assert body["status"] == "ok"
    assert body["n"] > 0
    assert body["failed"] == 0
    assert body["requested"] == body["n"] + body["failed"], "the invariant"
    assert body["reason_code"] is None, "nothing failed, so there is nothing to report"
    assert set(body["histogram"]) == {"edges", "counts"}


def test_requested_is_the_clamped_count_not_the_raw_input(client):
    """The endpoint clamps to [50, 5000]; `requested` reports what it will run."""
    body = post(client, n=1).json()

    assert body["requested"] == 50, "asking for 1 runs the floor, and says so"


def test_no_successful_run_is_a_failure_not_an_answer(client, monkeypatch):
    """The defect this file exists for: it used to be 200 with `{"error": ...}`."""
    monkeypatch.setattr(evaluate_module, "_simulate_once", always_raises(ValueError("boom")))

    r = post(client)

    assert r.status_code == 503, "computing nothing is not a distribution"
    body = r.json()
    assert body["status"] == "unavailable"
    assert body["reason_code"] == "no_successful_runs"
    assert body["n"] == 0
    assert body["failed"] == 60
    assert body["requested"] == 60
    assert body["requested"] == body["n"] + body["failed"], "the invariant holds on failure too"


def test_the_failure_body_carries_no_number_a_caller_could_chart(client, monkeypatch):
    monkeypatch.setattr(evaluate_module, "_simulate_once", always_raises(ValueError("boom")))

    body = post(client).json()

    for absent in ("mean", "p50", "histogram", "stdev"):
        assert absent not in body, absent


def test_the_exception_text_is_not_returned(client, monkeypatch):
    """`except Exception: continue` discarded the reason; nothing replaced it
    with the exception's text, and nothing should."""
    secret = "connection string postgresql://sora:hunter2@db/esg"
    monkeypatch.setattr(evaluate_module, "_simulate_once", always_raises(RuntimeError(secret)))

    assert "hunter2" not in post(client).text


def test_a_partial_sample_says_so_rather_than_shrinking_n_silently(client, monkeypatch):
    """`n` below `requested` used to be invisible: it read as the request."""
    calls = {"i": 0}

    def flaky(*_a, **_k):
        calls["i"] += 1
        if calls["i"] % 2:
            raise ValueError("every other one")
        return 61.5

    monkeypatch.setattr(evaluate_module, "_simulate_once", flaky)

    body = post(client).json()

    assert body["status"] == "ok"
    assert body["requested"] == 60
    assert 0 < body["n"] < 60, "half the simulations raised"
    assert body["failed"] == 60 - body["n"]
    assert body["requested"] == body["n"] + body["failed"], "the invariant"
    assert body["reason_code"] == "partial_sample", "the shortfall is stated, not inferred"


def test_the_retired_error_field_is_gone(client, monkeypatch):
    monkeypatch.setattr(evaluate_module, "_simulate_once", always_raises(ValueError("boom")))

    assert "error" not in post(client).json()


def test_openapi_declares_both_branches(client):
    schema = client.get("/openapi.json").json()
    op = schema["paths"]["/api/v1/evaluate/monte-carlo"]["post"]

    assert "503" in op["responses"], "the failure branch must be declared"

    components = schema["components"]["schemas"]
    assert OK_KEYS <= set(components["MonteCarloOk"]["properties"])
    assert {"status", "requested", "n", "failed", "reason_code"} <= set(
        components["MonteCarloUnavailable"]["properties"]
    )


# --- what happens to input the request model does not bound -------------------


def test_out_of_range_input_is_clamped_rather_than_raising():
    """Answers the question "should bad input be a 422 instead of a 503?".

    `_MCRequest` bounds nothing: a negative budget or a social score of 400
    passes validation. The endpoint does not reject them and does not fail on
    them either -- it clamps every field into the range `calculate_esg`
    accepts, before simulating. So invalid input does not become a thousand
    internal exceptions, and a 503 from this endpoint really does mean the
    service failed rather than the caller.

    Tested against `_clamped_sample`, which is pure and imports nothing. The
    first version replaced `sys.modules["app.main"]` with a stub, which worked
    locally under `--noconftest` and broke in CI: `conftest.py` does
    `from app.main import app`, and the stub had no `app`. Poisoning a shared
    module to test one function is too wide a blast radius -- the function is
    now separable instead.
    """
    req = evaluate_module._MCRequest(
        project_name="P", region="Nowhere",
        budget_usd=-5_000, co2_reduction_tons_per_year=-1,
        social_impact_score=400, project_duration_months=-12,
    )

    fields = evaluate_module._clamped_sample(req, lambda v: v)

    assert fields["budget_usd"] == 1000.0, "a negative budget is floored"
    assert fields["co2_reduction_tons_per_year"] == 1.0
    assert fields["social_impact_score"] == 10.0, "400 is capped at the scale's top"
    assert fields["project_duration_months"] == 1, "a negative duration is floored"


def test_the_clamps_leave_a_valid_sample_alone():
    """A negative control: clamping that changed everything would pass the
    test above while destroying the simulation."""
    req = evaluate_module._MCRequest(
        project_name="P", region="Sweden",
        budget_usd=150_000, co2_reduction_tons_per_year=120,
        social_impact_score=8, project_duration_months=24,
    )

    fields = evaluate_module._clamped_sample(req, lambda v: v)

    assert fields["budget_usd"] == 150_000
    assert fields["co2_reduction_tons_per_year"] == 120
    assert fields["social_impact_score"] == 8
    assert fields["project_duration_months"] == 24
