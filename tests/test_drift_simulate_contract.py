"""Every branch of POST /api/v1/mlops/drift/simulate.

Last migration in the series begun by #239.

The endpoint has always answered 200 in two different shapes: a simulation, and
a debounce skip carrying only `status`, `reason` and `observations`. Neither was
read. The frontend's success handler discards the response and announces the
mode it *asked for*, so a debounced call reported "Simulated stable" while
nothing had been simulated -- and the client type declared `status: "simulated"`
and nothing else, so the skip was invisible in types as well as at runtime.

The endpoint is destructive: it deletes `drift:observations` and refills it.
Everything here runs against a fake store, and nothing in this file may be
pointed at a system whose observations matter.
"""
from __future__ import annotations

import importlib
import sys
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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

baseline_module = importlib.import_module("app.api.drift_baseline")

KEYS = {"status", "mode", "shift_sigma", "shifts", "observations", "reason_code"}


class FakeRedis:
    """Enough Redis for this handler, and a switch to make it unreachable."""

    def __init__(self, *, broken=False, debounce_held=False):
        self.broken = broken
        self._held = debounce_held
        self.store: dict = {}
        self.deleted: list[str] = []

    def _check(self):
        if self.broken:
            raise ConnectionError("Error -3 connecting to redis:6379")

    def get(self, key):
        self._check()
        return self.store.get(key)

    def set(self, key, value, nx=False, ex=None):
        self._check()
        if nx and key == "drift:last_sim":
            return not self._held
        self.store[key] = value
        return True

    def delete(self, key):
        self._check()
        self.deleted.append(key)

    def rpush(self, *_a, **_k):
        self._check()

    def ltrim(self, *_a, **_k):
        self._check()


@pytest.fixture()
def detector(monkeypatch):
    """Point the module's detector at a fake store."""

    def install(*, broken=False, debounce_held=False, baseline=None, count=42):
        fake = FakeRedis(broken=broken, debounce_held=debounce_held)
        d = baseline_module.drift_detector
        monkeypatch.setattr(d, "_r", fake)
        monkeypatch.setattr(
            d, "get_baseline",
            lambda: (_ for _ in ()).throw(ConnectionError("down")) if broken
            else (baseline if baseline is not None else {"budget": {"mean": 1.0, "std": 1.0}}),
        )
        monkeypatch.setattr(d, "add_observation", lambda *_a, **_k: None)
        monkeypatch.setattr(d, "count", lambda: count)
        monkeypatch.setattr(d, "check_drift", lambda *_a, **_k: {"status": "ok"})
        return fake

    return install


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(baseline_module.router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


def post(client, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return client.post(f"/api/v1/mlops/drift/simulate{'?' + q if q else ''}")


def test_a_simulation_reports_what_it_applied(client, detector):
    detector()

    r = post(client, mode="drift", n=10)

    assert r.status_code == 200
    body = r.json()
    assert set(body) == KEYS
    assert body["status"] == "simulated"
    assert body["mode"] == "drift"
    assert body["shift_sigma"] == 5.0
    assert body["shifts"], "a drift run states which features it shifted"
    assert body["observations"] == 42
    assert body["reason_code"] is None


def test_a_debounced_call_says_nothing_was_simulated(client, detector):
    """The defect this file exists for.

    Both branches used to be 200 with different keys, and the caller read
    neither -- so "we ran one two seconds ago, try again" reached the user as
    "Simulated stable".
    """
    detector(debounce_held=True)

    r = post(client, mode="stable", n=10)

    assert r.status_code == 200, "being turned away is an answer, not a failure"
    body = r.json()
    assert set(body) == KEYS
    assert body["status"] == "skipped"
    assert body["reason_code"] == "debounced"
    assert body["mode"] is None, "no mode was applied; naming one would describe work not done"
    assert body["shift_sigma"] is None
    assert body["shifts"] == {}
    assert body["observations"] == 42


def test_a_debounced_call_does_not_touch_the_observations(client, detector):
    """The skip must be a skip: no deletion, no generated sample."""
    fake = detector(debounce_held=True)

    post(client, mode="drift", n=10)

    assert fake.deleted == [], "the observation window was left alone"


def test_an_unreachable_store_is_a_fault_not_an_empty_simulation(client, detector):
    detector(broken=True)

    r = post(client, mode="drift", n=10)

    assert r.status_code == 503, "this used to raise out of the handler as a 500"
    body = r.json()
    assert set(body) == KEYS
    assert body["status"] == "unavailable"
    assert body["reason_code"] == "observation_store_unavailable"
    assert body["observations"] is None, "zero would be a measurement nobody took"


def test_no_baseline_still_refuses_with_the_phrase_the_client_matches(client, detector):
    """400 and its wording are unchanged on purpose.

    The frontend distinguishes this case by `msg.includes("fit baseline
    first")` -- fragile, and tracked in issue 250, which needs a change to the
    API client rather than to this endpoint. Changing the phrase here would
    break the user-facing hint before that fix exists.
    """
    detector(baseline={})

    r = post(client, mode="drift", n=10)

    assert r.status_code == 400
    assert "fit baseline first" in r.text


def test_an_unknown_mode_is_refused_before_anything_is_deleted(client, detector):
    """422 from the query validator, not a simulation of something undefined."""
    fake = detector()

    r = post(client, mode="wipe-everything", n=10)

    assert r.status_code == 422
    assert fake.deleted == [], "nothing was touched"


def test_the_retired_reason_field_is_gone(client, detector):
    detector(debounce_held=True)

    assert "reason" not in post(client, mode="stable").json()


def test_openapi_declares_every_branch(client):
    schema = client.get("/openapi.json").json()
    op = schema["paths"]["/api/v1/mlops/drift/simulate"]["post"]

    for code in ("400", "422", "503"):
        assert code in op["responses"], code

    components = schema["components"]["schemas"]
    for model in ("DriftSimulateOk", "DriftSimulateSkipped", "DriftSimulateUnavailable"):
        assert model in components, model
        assert KEYS <= set(components[model]["properties"]), model

    assert components["DriftSimulateSkipped"]["properties"]["mode"].get("type") == "null"
    assert components["DriftSimulateUnavailable"]["properties"]["observations"].get("type") == "null"
