"""POST /mlops/auto-retrain activates a promoted candidate, or says it did not.

The endpoint applied the promotion gate and wrote `status="promoted"` to
`retrain_log`, but never called `activate()`. So it answered `promoted: true`
while the candidate stayed in `runtime/staged/<run_id>/` and nothing served it --
the one gated path that decided and then did nothing. `closed_loop_retrain`
activated; this did not.

Both paths now go through one helper, `activate_promoted_candidate`, so a
promotion means the same thing on both: the gate approved and activation was
attempted. The response says whether it took (`activated`) and reports any
`activation_error` rather than claiming a promotion that never reached
`active/`. A failure to activate does not turn the gate's yes into a no; it is
reported, and the previous champion keeps serving.
"""
import uuid

import pytest

from app.model_source import Baseline


def _drift(detected):
    class _D:
        status = "ok"
        drift_detected = detected

        def model_dump(self):
            return {"status": self.status, "drift_detected": detected}

    return _D()


@pytest.fixture
def wired(monkeypatch):
    """Drive auto_retrain_on_drift with the heavy collaborators stubbed and
    activate/reload spied on."""
    import app.api.drift as drift_module
    import app.api.retrain as retrain_module
    import app.model_source as model_source
    import app.model_loader as model_loader

    run_id = str(uuid.uuid4())
    calls = {"activate": [], "reload": 0}

    monkeypatch.setattr(drift_module, "compute_drift", lambda **k: _drift(True), raising=False)
    monkeypatch.setattr(retrain_module, "_do_retrain",
                        lambda **k: {"run_id": run_id, "metrics": _metrics(0.90),
                                     "retrain_log_id": None}, raising=False)
    monkeypatch.setattr(model_source, "serving_baseline",
                        lambda: Baseline(auc=0.80, source="active", ok=True), raising=False)
    monkeypatch.setattr(model_source, "activate",
                        lambda rid: calls["activate"].append(rid), raising=False)

    def _reload():
        calls["reload"] += 1

    monkeypatch.setattr(model_loader, "reload_champion", _reload, raising=False)
    return {"run_id": run_id, "calls": calls, "retrain": retrain_module, "monkeypatch": monkeypatch}


def _metrics(auc):
    return {"roc_auc": auc, "test_positive": 500, "test_negative": 500}


def _run():
    import app.api.infra as infra

    return infra.auto_retrain_on_drift(
        window=50, min_samples=20, force=True, current_user={"username": "t", "role": "admin"}
    )


def test_a_promoted_candidate_is_activated_and_reloaded(wired):
    result = _run()

    assert result["promoted"] is True
    assert wired["calls"]["activate"] == [wired["run_id"]], "the promoted run was not activated"
    assert wired["calls"]["reload"] == 1, "the champion was not reloaded after activation"
    assert result["activated"] is True
    assert result.get("activation_error") is None


def test_a_rejected_candidate_is_not_activated(wired):
    wired["monkeypatch"].setattr(wired["retrain"], "_do_retrain",
                                 lambda **k: {"run_id": wired["run_id"], "metrics": _metrics(0.60),
                                              "retrain_log_id": None}, raising=False)
    result = _run()

    assert result["promoted"] is False
    assert wired["calls"]["activate"] == [], "a rejected candidate was activated"
    assert result["activated"] is False


def test_a_failed_activation_is_reported_without_undoing_the_verdict(wired):
    import app.model_source as model_source

    def _boom(_rid):
        raise RuntimeError("staged candidate is still being written")

    wired["monkeypatch"].setattr(model_source, "activate", _boom, raising=False)
    result = _run()

    assert result["promoted"] is True, "an activation failure must not turn the gate's yes into a no"
    assert result["activated"] is False
    assert "RuntimeError" in (result.get("activation_error") or "")


def test_both_gated_paths_use_the_shared_activation_helper():
    """No inline activate(...) in either caller: one activation sequence."""
    import inspect

    import app.scheduler as scheduler
    import app.api.infra as infra

    for func in (scheduler.closed_loop_retrain, infra.auto_retrain_on_drift):
        source = inspect.getsource(func)
        assert "activate_promoted_candidate" in source, (
            "%s does not go through the shared activation helper" % func.__name__
        )


def test_the_helper_activates_then_reloads_and_swallows_nothing_silently(monkeypatch):
    import app.model_source as model_source
    import app.model_loader as model_loader

    order = []
    monkeypatch.setattr(model_source, "activate", lambda rid: order.append(("activate", rid)), raising=False)
    monkeypatch.setattr(model_loader, "reload_champion", lambda: order.append(("reload",)), raising=False)

    err = model_loader.activate_promoted_candidate("abc")
    assert err is None
    assert order == [("activate", "abc"), ("reload",)], "activation must precede the reload"

    def _boom(_rid):
        raise ValueError("nope")

    monkeypatch.setattr(model_source, "activate", _boom, raising=False)
    err = model_loader.activate_promoted_candidate("abc")
    assert err is not None and "ValueError" in err

    # No run id is nothing to activate, not an error.
    assert model_loader.activate_promoted_candidate(None) is None
