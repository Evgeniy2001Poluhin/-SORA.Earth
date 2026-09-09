"""`POST /api/v1/predict/v2` must not answer an outage with a prediction.

Migration under `docs/API_CONTRACT_ROADMAP.md` §4, and the same trade #247
made for MLflow history: a fault stops being a 200.

The registry being unreachable used to answer **200** with:

    {"success_probability": null,
     "error": "registry_unavailable",
     "fallback_to_v1": true,
     "model": {...}}

Three separate problems in one body:

    a null probability rendered on a screen is 0 -- "this project will fail",
    which is a prediction and not an outage
    `predicted_class` was absent entirely, so `body.get("predicted_class")`
    is None, which a naive consumer coerces to 0: the same wrong verdict by
    another route
    `fallback_to_v1: true` named an action. Nothing in the handler falls back,
    and the flag was read nowhere -- measured across `app/`, `tests/`,
    `web/src` and the built bundle: one write, zero reads

The flag is removed rather than documented, which is the part worth arguing
for: a field that says the system did something it did not is worse than no
field at all.

Measured before changing anything: no frontend module, test or script calls
this route. Only `docs/ARCHITECTURE.md` mentions it, in a list. So turning the
failure into a 503 breaks no caller that exists.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
URL = "/api/v1/predict/v2"
BODY = {
    "budget": 100000,
    "co2_reduction": 300,
    "social_impact": 7,
    "duration_months": 12,
    "category": "solar",
    "region": "EU",
}


@pytest.fixture()
def registry(monkeypatch):
    """The registry, answering or not, with nothing else stubbed."""
    from app import ml_registry

    def answer(value):
        monkeypatch.setattr(ml_registry, "predict_proba", lambda *a, **k: value)

    monkeypatch.setattr(ml_registry, "info", lambda: {"name": "rf", "stage": "Production"})
    return answer


def test_the_route_declares_both_shapes():
    """The migration itself, at the route table.

    Every assertion below describes a body, and all of them stay green if the
    declaration is deleted -- that was measured on the neighbouring migration
    and it is the same risk here. What makes this a contract is that the
    shapes reach `/openapi.json`.
    """
    route = next(r for r in app.routes if getattr(r, "path", "") == URL)

    assert route.response_model is not None, f"{URL} declares no response_model"
    assert getattr(route.response_model, "__name__", "") == "PredictV2Ok"
    assert 503 in (route.responses or {}), (
        "the failure shape is not in the schema, so it exists only in prose"
    )


def test_a_prediction_is_a_prediction(registry):
    registry(0.7321)
    r = client.post(URL, json=BODY)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success_probability"] == pytest.approx(0.7321)
    assert body["predicted_class"] == 1
    assert "error" not in body and "fallback_to_v1" not in body


def test_an_unreachable_registry_is_not_a_verdict(registry):
    """The defect, stated as the acceptance criterion.

    503, and no place in the body where a probability is supposed to be.
    """
    registry(None)
    r = client.post(URL, json=BODY)

    assert r.status_code == 503, (
        f"an outage answered {r.status_code}; at 200 it is indistinguishable "
        f"from a prediction of failure"
    )
    body = r.json()
    assert body["reason_code"] == "registry_unavailable"
    assert "success_probability" not in body, (
        "a null probability is what rendered as 0 and read as 'will fail'"
    )
    assert "predicted_class" not in body, (
        "an absent class coerces to 0, which is the same wrong verdict"
    )


def test_the_flag_that_named_an_action_nobody_performed_is_gone(registry):
    """`fallback_to_v1: true` was written once and read never.

    Asserted on both branches, because a field that returns on only one of
    them is exactly how it survived unnoticed the first time.
    """
    registry(None)
    assert "fallback_to_v1" not in client.post(URL, json=BODY).json()

    registry(0.5)
    assert "fallback_to_v1" not in client.post(URL, json=BODY).json()


def test_no_consumer_reads_that_flag():
    """The measurement the removal rests on, kept as an assertion.

    Scoped to `web/src`, and that is the whole design of this check. The first
    version swept `app/` and `tests/` too and went red on **its own
    explanation**: this file quotes the old body, and `app/schemas.py` and
    `app/api/predict_v2.py` describe what was removed and why. A rule that
    matches prose about itself cannot refuse anything -- the third time this
    exact shape appeared in one day.

    The consumer side has no such prose. If a frontend module starts reading
    the flag, this goes red and the removal has to be argued again rather than
    assumed.
    """
    import subprocess

    def grep(token):
        return subprocess.run(
            ["git", "grep", "-l", token, "--", "web/src"],
            capture_output=True, text=True,
        ).stdout.split()

    # Discriminating case first: a token that is certainly there, so "no hits"
    # cannot mean "the search reached nothing".
    assert grep("success_probability"), (
        "the search finds nothing in web/src at all; it is not reaching the "
        "frontend, and the assertion below would pass over an empty tree"
    )

    assert not grep("fallback_to_v1"), (
        f"the frontend reads a flag the server no longer sends: "
        f"{grep('fallback_to_v1')}"
    )


def test_the_boundary_class_is_not_off_by_one(registry):
    """0.5 is a positive class. Stated because the rounding sits next to it."""
    registry(0.5)
    assert client.post(URL, json=BODY).json()["predicted_class"] == 1
    registry(0.4999)
    assert client.post(URL, json=BODY).json()["predicted_class"] == 0
