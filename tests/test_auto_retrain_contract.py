"""`POST /api/v1/mlops/auto-retrain` must declare which of its answers it gave.

Migration under `docs/API_CONTRACT_ROADMAP.md` §4, priority **P0**: "a wrong or
absent verdict is acted on as a decision".

The handler returned two different bodies at HTTP 200 and named neither:

    retrain ran        status, drift_detected, drift_result, retrained,
                       forced, promoted, old_auc, new_auc, reject_reason,
                       retrain_result, finalisation_error
    retrain did not    status, drift_detected, drift_result, retrained, reason

So `promoted` was absent both when no model was trained and when the field was
simply not sent, and a caller reading `body.get("promoted")` got `None` for
both. That is the shape of every defect in this roadmap: the server does not
say what it returns, so the consumer guesses.

**Discriminated on `retrained`, not on `status`.** `status` does not separate
the cases: "the drift check could not run" is `skipped` and "drift was measured
and there is none" is `ok`, and before this change both carried the same body.
What a caller has to branch on is whether a model was trained.

`retrain_result` stays untyped on purpose. It is `_do_retrain`'s contract, and
declaring a shape for it here would be asserting something nobody measured --
its own migration, its own PR.

**No real retrain runs in this file.** `_do_retrain` writes six tracked model
files and hot-swaps the serving model before any gate decides; a test that
called it would rewrite the repository. It is stubbed everywhere below.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import require_admin
from app.main import app
from app.schemas import ModelDriftMeasured, ModelDriftNotMeasured, ModelDriftUnavailable

client = TestClient(app)
URL = "/api/v1/mlops/auto-retrain"


@pytest.fixture(autouse=True)
def _as_admin():
    app.dependency_overrides[require_admin] = lambda: {"username": "t", "role": "admin"}
    yield
    app.dependency_overrides.pop(require_admin, None)


def _drift(status: str, detected=None):
    """A drift answer of each kind, built from the declared models."""
    common = dict(window=50, observations=100, features={}, reason_code=None)
    if status == "ok":
        return ModelDriftMeasured(status="ok", drift_detected=detected, **common)
    if status == "unavailable":
        return ModelDriftUnavailable(status="unavailable", **common)
    return ModelDriftNotMeasured(status=status, **{**common, "observations": 0})


@pytest.fixture()
def stub(monkeypatch):
    """Everything the handler calls, so only its own branching is under test."""
    import app.api.drift as drift_module
    import app.api.infra as infra
    import app.api.retrain as retrain_module
    from app.promotion import PromotionDecision

    state = {"retrained": 0}

    def _fake_retrain(**kwargs):
        state["retrained"] += 1
        return {"status": "ok", "metrics": {"auc_roc": 0.91}, "retrain_log_id": None}

    monkeypatch.setattr(retrain_module, "_do_retrain", _fake_retrain, raising=False)
    monkeypatch.setattr(retrain_module, "_get_current_metrics",
                        lambda: {"auc_roc": 0.80}, raising=False)
    monkeypatch.setattr(
        infra, "evaluate_promotion",
        lambda metrics, old: PromotionDecision(promoted=True, reject_reason=None),
        raising=False,
    )
    state["drift_module"] = drift_module
    state["monkeypatch"] = monkeypatch
    return state


def _set_drift(stub, answer):
    stub["monkeypatch"].setattr(stub["drift_module"], "compute_drift",
                                lambda **kw: answer, raising=False)


def test_the_route_declares_the_contract():
    """The migration itself, asserted where it lives: the route table.

    Every other test here describes what the handler returns, and all of them
    stay green if `response_model` is deleted -- measured. The declaration is
    what turns those bodies into a contract: it is what reaches
    `/openapi.json`, what the frontend types are generated from, and what the
    ratchet counts. Without this assertion the migration could be undone and
    only the committed manifest would notice.
    """
    route = next(r for r in app.routes if getattr(r, "path", "") == URL)

    assert route.response_model is not None, (
        f"{URL} declares no response_model; the two shapes are undeclared again"
    )
    members = getattr(route.response_model, "__args__", ())
    names = {getattr(m, "__name__", str(m)) for m in members[0].__args__} if members else set()
    assert {"AutoRetrainNotRun", "AutoRetrainRan"} <= names, (
        f"the declared model is no longer the discriminated union: {names}"
    )


def test_the_endpoint_answers_at_all(stub):
    """Negative control. Every assertion below reads this endpoint's body."""
    _set_drift(stub, _drift("ok", detected=False))
    r = client.post(URL)
    assert r.status_code == 200, r.text
    assert "retrained" in r.json(), r.json()


@pytest.mark.parametrize(
    "drift_status, expected_reason, expected_status",
    [
        ("unavailable", "drift_check_unavailable", "skipped"),
        ("no_log", "drift_not_measured", "skipped"),
        ("insufficient_data", "drift_not_measured", "skipped"),
    ],
)
def test_a_verdict_that_could_not_be_reached_says_so(
    stub, drift_status, expected_reason, expected_status
):
    """Nothing was measured, so nothing was decided and nothing was trained."""
    _set_drift(stub, _drift(drift_status))
    body = client.post(URL).json()

    assert body["retrained"] is False
    assert body["status"] == expected_status
    assert body["reason"] == expected_reason
    assert body["drift_detected"] is None, (
        "false would assert that drift was looked for and not found"
    )
    assert stub["retrained"] == 0, "a model was trained without a verdict"
    assert "promoted" not in body, (
        "a promotion field on a run that trained nothing is what made the two "
        "shapes indistinguishable"
    )


def test_no_drift_is_a_verdict_and_not_a_skip(stub):
    """Measured and negative: `status` is `ok`, and nothing was trained."""
    _set_drift(stub, _drift("ok", detected=False))
    body = client.post(URL).json()

    assert body["status"] == "ok", "a reached verdict is not a skip"
    assert body["retrained"] is False
    assert body["drift_detected"] is False
    assert body["reason"] == "drift_not_detected"
    assert stub["retrained"] == 0


def test_a_run_that_trained_carries_the_promotion_verdict(stub):
    """The other shape, and the fields that exist only here."""
    _set_drift(stub, _drift("ok", detected=True))
    body = client.post(URL).json()

    assert body["retrained"] is True
    assert body["status"] == "ok"
    assert body["drift_detected"] is True
    assert body["promoted"] is True
    assert body["forced"] is False
    assert body["old_auc"] == pytest.approx(0.80)
    assert body["new_auc"] == pytest.approx(0.91)
    assert stub["retrained"] == 1


def test_force_overrides_an_absent_verdict(stub):
    """`force` means "retrain regardless", including regardless of no verdict.

    The branch that matters: without it, an unavailable check skips. With it,
    the run happens and the answer is the *other* shape -- which a caller can
    now tell apart without knowing which query parameters were sent.
    """
    _set_drift(stub, _drift("unavailable"))
    body = client.post(URL, params={"force": "true"}).json()

    assert body["retrained"] is True
    assert body["forced"] is True
    assert stub["retrained"] == 1


def test_the_two_shapes_are_told_apart_by_one_field(stub):
    """The property the union exists for, stated once.

    A consumer branches on `retrained` and every field it then reads is
    declared present. Before this, it had to probe for `promoted` and could not
    distinguish "no model was trained" from "the field was not sent".
    """
    _set_drift(stub, _drift("ok", detected=False))
    not_run = client.post(URL).json()
    _set_drift(stub, _drift("ok", detected=True))
    ran = client.post(URL).json()

    assert not_run["retrained"] is False and ran["retrained"] is True

    only_when_run = {"forced", "promoted", "old_auc", "new_auc",
                     "reject_reason", "retrain_result", "finalisation_error"}
    assert only_when_run <= set(ran), sorted(only_when_run - set(ran))
    assert not (only_when_run & set(not_run)), sorted(only_when_run & set(not_run))
