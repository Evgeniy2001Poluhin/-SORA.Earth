"""The A/B routes must not answer a failure with 200 (roadmap §4).

Two routes in one file, both with the shape this roadmap exists to remove, and
one of them is worse than the other because it is a **write**.

    POST /ab/predict   any exception answered 200 with {"error": str(e)}.
                       No `probability` field, so a consumer reading it finds
                       nothing where a number belongs -- and the exception
                       text went to the caller, which #247 already removed
                       from the MLflow path.

    POST /ab/split     an out-of-range percentage answered 200 with
                       {"error": "must be 0.0-1.0"} and left the split alone.
                       A caller that sent 5.0 was told 200 and went on
                       believing the split was 5.0.

The split range is now a constraint on the parameter, so FastAPI refuses it
with 422 before the handler runs. The branch is not fixed but removed: a
validation rule written as an `if` inside a handler applies only where somebody
remembered to write it, and never reaches `/openapi.json`.

Measured before changing anything: nothing outside `tests/` calls these routes,
and the existing `tests/test_ab.py` exercises only the success path and
pydantic's own 422. No consumer sees the change.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
PREDICT = "/api/v1/ab/predict"
SPLIT = "/api/v1/ab/split"
SAMPLE = {"budget": 75000, "co2_reduction": 150, "social_impact": 9, "duration_months": 18}


@pytest.fixture(autouse=True)
def _restore_split():
    """The split is module-global. A test that moves it must put it back."""
    from app.api.ab_test import _traffic_split

    before = dict(_traffic_split)
    yield
    _traffic_split.clear()
    _traffic_split.update(before)


def test_both_routes_declare_their_shapes():
    """The migration itself, read from the route table.

    Every assertion below describes a body, and all of them stay green if the
    declaration is deleted -- measured on the neighbouring migration, twice.
    """
    routes = {r.path: r for r in app.routes if getattr(r, "path", "") in (PREDICT, SPLIT)}
    assert set(routes) == {PREDICT, SPLIT}, sorted(routes)

    assert getattr(routes[PREDICT].response_model, "__name__", "") == "ABPredictOk"
    assert 503 in (routes[PREDICT].responses or {}), (
        "the failure shape is not in the schema, so it exists only in prose"
    )
    assert getattr(routes[SPLIT].response_model, "__name__", "") == "ABSplitOk"


def test_a_prediction_still_answers_normally():
    """Negative control: the success path is untouched."""
    r = client.post(PREDICT, json=SAMPLE)

    assert r.status_code == 200, r.text
    body = r.json()
    assert 0.0 <= body["probability"] <= 1.0
    assert body["prediction"] in ("approved", "rejected")
    assert "error" not in body


def test_a_model_that_cannot_answer_is_not_a_prediction(monkeypatch):
    """The defect: a 200 with no probability in it."""
    import app.main as main_module

    class _Broken:
        def predict_proba(self, *a, **k):
            raise RuntimeError("column 'budget_per_month' is missing from /srv/models")

    monkeypatch.setattr(main_module, "rf_model", _Broken(), raising=False)
    monkeypatch.setattr(main_module, "ensemble_model_v2", None, raising=False)

    r = client.post(PREDICT, json=SAMPLE)

    assert r.status_code == 503, (
        f"a failed prediction answered {r.status_code}; at 200 a consumer "
        f"reading .probability finds nothing where a number belongs"
    )
    body = r.json()
    assert body["reason_code"] == "model_unavailable"
    assert "probability" not in body


def test_the_failure_does_not_echo_the_exception(monkeypatch):
    """The other half of the same branch, and its own property.

    `str(e)` named a column and a filesystem path in the case above. Neither
    is actionable for a caller, and both describe the inside of the server.
    """
    import app.main as main_module

    class _Broken:
        def predict_proba(self, *a, **k):
            raise RuntimeError("column 'budget_per_month' is missing from /srv/models")

    monkeypatch.setattr(main_module, "rf_model", _Broken(), raising=False)
    monkeypatch.setattr(main_module, "ensemble_model_v2", None, raising=False)

    text = client.post(PREDICT, json=SAMPLE).text

    for leaked in ("budget_per_month", "/srv/models", "RuntimeError"):
        assert leaked not in text, f"the response carries {leaked!r} from the exception"


def test_a_split_that_is_refused_does_not_move_the_split():
    """The write that silently did not happen.

    Both halves matter: the caller is told 422, **and** the value is unchanged.
    The old code returned 200 and also left it unchanged, which is why nobody
    noticed -- the lie was in the status, not in the state.
    """
    from app.api.ab_test import _traffic_split

    before = dict(_traffic_split)
    r = client.post(SPLIT, json={"model_a_pct": 5.0})

    assert r.status_code == 422, (
        f"an out-of-range split answered {r.status_code}; at 200 the caller "
        f"believes it took"
    )
    assert dict(_traffic_split) == before, "the refused value moved the split anyway"


def test_a_split_inside_the_range_takes_effect():
    """The discriminating case: the refusal above must not be refusing everything."""
    from app.api.ab_test import _traffic_split

    r = client.post(SPLIT, json={"model_a_pct": 0.25})

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    assert _traffic_split["model_a"] == pytest.approx(0.25)
    assert r.json()["traffic_split"]["model_a"] == pytest.approx(0.25)


@pytest.mark.parametrize("value", [0.0, 1.0])
def test_the_bounds_themselves_are_accepted(value):
    """`ge`/`le`, not `gt`/`lt`: all traffic to one arm is a legitimate setting."""
    assert client.post(SPLIT, json={"model_a_pct": value}).status_code == 200
