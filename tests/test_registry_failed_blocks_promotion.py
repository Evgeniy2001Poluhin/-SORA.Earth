"""A model that did not register is not promoted, and that is driven, not read (#189).

Migrated from PR #198, which is closed without merging: its own status model —
a `registry_failed` value in `status` — was superseded by phase 2B, where the
stages are separate columns. The behaviour it proved is unchanged and worth
keeping, so the tests move and the assertions follow the current shape.

`tests/test_registration_gates_promotion.py` asserts that `app/scheduler.py`
*contains* the right lines. That text is identical whether the gate runs, is
short-circuited by an earlier refusal, or never receives the field at all. These
run `closed_loop_retrain` and read its decision.

Everything replaced below is an **input** to the decision: the lock, the drift
check, the previous metrics, and the retrain. All three gates are the real ones.

The control case carries the weight. A test that only asserted "not promoted"
when registration failed would pass just as well if the AUC gate were doing the
refusing, and would keep passing with the registry gate deleted.
"""
import pytest

from app import mlflow_tracking
from app import scheduler as sched

def _drift(detected: bool):
    """A drift result shaped like the one `compute_drift` really returns.

    These tests used to patch `app.api.drift.check_drift` with a plain dict.
    That mock agreed with the caller while the real function stopped agreeing:
    the contract migration changed the return to a Pydantic model, and
    `drift_result.get(...)` would have raised AttributeError in production
    without a single test going red. The mock now has the shape the real
    function produces, so the next such change fails here.
    """
    from app.schemas import ModelDriftMeasured

    return ModelDriftMeasured(
        status="ok", drift_detected=detected, window=50,
        observations=100, features={}, reason_code=None,
    )


class _HeldLock:
    def acquire(self):
        return True

    def release(self):
        pass


#: AUC 0.9063 on 3,400 test rows, so the 95% lower bound clears 0.80 and gate 1
#: has no reason to refuse. Both class counts present, so the bound is used
#: rather than the point estimate.
GOOD_METRICS = {
    "roc_auc": 0.9063,
    "accuracy": 0.87,
    "f1_score": 0.83,
    "test_positive": 800,
    "test_negative": 2600,
    "split_kind": "stratified_by_outcome",
}


def run_closed_loop(monkeypatch, new_metrics, old_auc=0.85):
    """Run the real decision over a chosen retrain outcome; return it and the journal."""
    journal = {}

    monkeypatch.setattr(sched, "_start_retrain_log", lambda **kwargs: 1)
    monkeypatch.setattr(sched, "_finish_retrain_log", lambda **kwargs: journal.update(kwargs))
    monkeypatch.setattr("app.locks.RedisLock", lambda **kwargs: _HeldLock())
    monkeypatch.setattr("app.api.drift.compute_drift", lambda window=50: _drift(True))
    monkeypatch.setattr("app.api.retrain._get_current_metrics", lambda: {"roc_auc": old_auc})
    monkeypatch.setattr(
        "app.api.retrain._do_retrain",
        lambda **kwargs: {"status": "success", "metrics": dict(new_metrics)},
    )

    result = sched.closed_loop_retrain(trigger_source="test_registry_gate")
    assert result.get("status") == "ok", f"the loop did not complete: {result}"
    return result, journal


def test_a_model_that_did_not_register_is_not_promoted(monkeypatch):
    result, journal = run_closed_loop(monkeypatch, {**GOOD_METRICS, "registry_ok": False})

    assert result["promoted"] is False
    assert "registry_failed" in (result["reject_reason"] or "")
    assert journal["promotion_status"] == "rejected"
    assert "registry_failed" in (journal["failure_reason"] or "")


def test_the_same_model_is_promoted_once_it_did_register(monkeypatch):
    """The control. Without it the test above proves nothing about the registry.

    Same numbers, same gates, one field different. If this run is refused too,
    something other than registration is doing the refusing.
    """
    result, journal = run_closed_loop(monkeypatch, {**GOOD_METRICS, "registry_ok": True})

    assert result["promoted"] is True, f"refused for another reason: {result['reject_reason']}"
    assert journal["promotion_status"] == "promoted"


def test_a_run_recorded_before_the_field_existed_keeps_its_old_treatment(monkeypatch):
    """Absent is unknown, and unknown must not be read as failure.

    Every run before #189 carries no `registry_ok`. Refusing them retroactively
    would rewrite the meaning of history recorded under a different rule — which
    is what `if not registry_ok` would do, and why the code says `is False`.
    """
    metrics = dict(GOOD_METRICS)
    assert "registry_ok" not in metrics

    result, journal = run_closed_loop(monkeypatch, metrics)

    assert result["promoted"] is True, f"an old run was refused: {result['reject_reason']}"
    assert journal["promotion_status"] == "promoted"


def test_the_refusal_does_not_deny_the_training_that_happened(monkeypatch):
    """`registry_failed` is not `failed`.

    The model was trained, evaluated and staged. Reporting the run as failed
    would deny work that happened; reporting it as a success would claim a step
    that did not. The journal carries both facts at once — the promotion was
    refused, and the training is not marked failed.
    """
    result, journal = run_closed_loop(monkeypatch, {**GOOD_METRICS, "registry_ok": False})

    assert journal["promotion_status"] == "rejected"
    assert journal.get("training_status") is None, (
        "the decision row claimed something about the training it did not perform"
    )
    assert journal["metrics"]["new_auc"] == pytest.approx(0.9063)
    assert journal["metrics"]["promoted"] is False
    assert result["new_auc"] == pytest.approx(0.9063)


def test_a_quality_refusal_is_still_reported_as_a_plain_rejection(monkeypatch):
    """The other kind must keep its own reason, or the distinction is decorative.

    A model refused on AUC and one refused for not registering call for opposite
    responses: the first needs the model left alone, the second needs the
    registry looked at.
    """
    poor = {**GOOD_METRICS, "roc_auc": 0.55, "registry_ok": True}
    result, journal = run_closed_loop(monkeypatch, poor)

    assert result["promoted"] is False
    assert journal["promotion_status"] == "rejected"
    assert "registry_failed" not in (result["reject_reason"] or "")


def test_offline_arrives_at_the_gate_as_not_registered(monkeypatch):
    """End to end over the seam: offline produces False, and False refuses promotion.

    `log_model_registry` is the real one, called offline, and its answer is what
    the gate reads — so "we did not try" reaches the decision as "it is not
    registered" rather than quietly as success.
    """
    monkeypatch.setattr(mlflow_tracking, "_OFFLINE", True)
    registry_ok = mlflow_tracking.log_model_registry(object(), "RandomForest_retrain", {"auc": 0.9})
    assert registry_ok is False

    result, journal = run_closed_loop(
        monkeypatch, {**GOOD_METRICS, "registry_ok": bool(registry_ok)}
    )

    assert result["promoted"] is False
    assert "registry_failed" in (result["reject_reason"] or "")
    assert journal["promotion_status"] == "rejected"
