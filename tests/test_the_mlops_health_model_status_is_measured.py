"""`/mlops/health` reports the model status of the process, not a constant.

`GET /api/v1/mlops/health` answered `"model_status": "healthy"` as a literal,
whatever the process had loaded. `MlopsHealthPage` draws that field as its
model-status KPI -- green for `healthy`, red for anything else -- so a worker
whose champion failed to load showed a green HEALTHY.

The same literal was taken out of `/health` and `/system/health` in #324, and
out of `/analytics/metrics/model-health` later. This endpoint was on neither
list. It was found by tracing the green KPI on the MLOps page back to where the
value is produced.

The only test that called the endpoint, `tests/test_k8s_readiness.py`, asserts
that `model_status` is *present*. A constant passes that on every run.

## The fix, and why it is not a fourth copy

The status now comes from `app.api.system._check_models()`, which the readiness
probe and the public status page already use. The repository already holds
three definitions of "the champion is loaded": that one, `app.main._models_loaded`
and an inline condition in `app/api/analytics.py`. Adding a fourth would give
the question another answer that can drift away from the others.

`_check_models()` answers `healthy`, `degraded` or, if the models cannot even be
imported, `unhealthy`. All three fit the page: only `healthy` is drawn green.

## How this is tested

The champion is set by replacing `app.main.rf_model` and `app.main.xgb_model`,
the names `_check_models()` reads at call time. The drift half of the response
comes from a real `DriftDetector` whose Redis is a dictionary, installed as
`app.api.infra.drift_detector` -- the name that module bound at import.

Each state is driven both ways. The loaded champion is the control: a fix that
answered `degraded` unconditionally would pass the unloaded cases alone.
"""

from __future__ import annotations

import pytest


class _MemoryRedis:
    """The calls `DriftDetector` makes, over two dictionaries."""

    def __init__(self):
        self.kv: dict = {}
        self.lists: dict = {}

    def get(self, key):
        return self.kv.get(key)

    def set(self, key, value):
        self.kv[key] = value if isinstance(value, str) else str(value)
        return True

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    def ltrim(self, key, start, end):
        items = self.lists.get(key, [])
        self.lists[key] = items[start:] if end == -1 else items[start:end + 1]

    def lrange(self, key, start, end):
        items = self.lists.get(key, [])
        return items[start:] if end == -1 else items[start:end + 1]

    def llen(self, key):
        return len(self.lists.get(key, []))


@pytest.fixture()
def health(monkeypatch):
    """Call the endpoint with the champion in a chosen state."""
    from fastapi.testclient import TestClient

    import app.api.infra as infra
    import app.drift_detection as dd
    import app.main as main

    monkeypatch.setattr(dd.redis, "from_url", lambda *a, **k: _MemoryRedis())
    monkeypatch.setattr(infra, "drift_detector", dd.DriftDetector(min_samples=5))
    client = TestClient(main.app)

    loaded = object()

    def call(*, rf: bool, xgb: bool) -> dict:
        monkeypatch.setattr(main, "rf_model", loaded if rf else None)
        monkeypatch.setattr(main, "xgb_model", loaded if xgb else None)
        response = client.get("/api/v1/mlops/health")
        assert response.status_code == 200, response.text
        return response.json()

    return call


def test_a_loaded_champion_is_reported_healthy(health):
    """Control: the measured positive must survive the fix."""
    assert health(rf=True, xgb=True)["model_status"] == "healthy"


@pytest.mark.parametrize(
    "rf, xgb",
    [(False, True), (True, False), (False, False)],
    ids=["no-randomforest", "no-xgboost", "nothing-loaded"],
)
def test_a_champion_that_did_not_load_is_not_reported_healthy(health, rf, xgb):
    answer = health(rf=rf, xgb=xgb)
    assert answer["model_status"] != "healthy", (
        f"/mlops/health reported model_status='healthy' with rf_model "
        f"{'loaded' if rf else 'None'} and xgb_model "
        f"{'loaded' if xgb else 'None'}; the MLOps page draws that as a "
        f"green HEALTHY"
    )
    assert answer["model_status"] == "degraded", answer["model_status"]


@pytest.mark.parametrize(
    "rf, xgb", [(True, True), (False, True), (True, False), (False, False)])
def test_it_agrees_with_the_definition_the_readiness_probe_uses(health, rf, xgb):
    """One answer to "is the champion loaded", not a fourth -- in all four states."""
    from app.api.system import _check_models

    answer = health(rf=rf, xgb=xgb)
    assert answer["model_status"] == _check_models()["status"]


def test_the_drift_half_still_comes_from_the_detector(health):
    """The fix must not disturb the fields beside it."""
    answer = health(rf=True, xgb=True)
    assert answer["drift_status"] == "insufficient_data", answer
    assert answer["observations_tracked"] == 0, answer
