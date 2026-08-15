"""`registry_failed` must survive from the registry call to the promotion refusal (#189).

`tests/test_registration_gates_promotion.py` checks that `app/scheduler.py`
*contains* the right lines. That cannot fail if the gate is reordered, if an
earlier refusal short-circuits it, or if `new_metrics` never carries the field
in the first place -- the text would be identical in every case. These tests run
`closed_loop_retrain` and read its decision.

Everything replaced below is an **input** to the decision: the lock, the drift
check, the previous metrics, and the retrain itself. The gate that reads
`registry_ok` and the two gates around it are the real ones, so a change to any
of them shows up here.

The control case is the load-bearing part. A test that only asserted "not
promoted" when registration failed would pass just as well if the AUC gate were
refusing the model for its own reasons, and would keep passing if the registry
gate were deleted. So the same metrics are run twice, differing only in
`registry_ok`, and the run that registered must be promoted.
"""
import pytest

from app import scheduler as sched
from app import mlflow_tracking


class _HeldLock:
    """The loop takes a Redis lock; the decision under test is not the locking."""

    def acquire(self):
        return True

    def release(self):
        pass


#: An honestly good model: AUC 0.9063 on 3,400 test rows, so the 95% lower
#: bound clears 0.80 comfortably and gate 1 has no reason to refuse. Both class
#: counts are present, so the bound is used rather than the point estimate.
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
    monkeypatch.setattr("app.api.drift.check_drift", lambda window=50: {"drift_detected": True})
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
    assert journal["status"] == "rejected"


def test_the_same_model_is_promoted_once_it_did_register(monkeypatch):
    """The control. Without it, the test above proves nothing about the registry.

    Same numbers, same gates, one field different. If this run is refused too,
    something other than registration is doing the refusing and the assertion
    above is measuring the wrong thing.
    """
    result, journal = run_closed_loop(monkeypatch, {**GOOD_METRICS, "registry_ok": True})

    assert result["promoted"] is True, f"refused for another reason: {result['reject_reason']}"
    assert journal["status"] == "promoted"


def test_a_run_recorded_before_the_field_existed_keeps_its_old_treatment(monkeypatch):
    """Absent is unknown, and unknown must not be read as failure.

    Every run before #189 carries no `registry_ok`. Refusing them retroactively
    would rewrite the meaning of history that was recorded under a different
    rule -- and `if not registry_ok` would do exactly that, which is why the
    code says `is False`.
    """
    metrics = dict(GOOD_METRICS)
    assert "registry_ok" not in metrics

    result, journal = run_closed_loop(monkeypatch, metrics)

    assert result["promoted"] is True, f"an old run was refused: {result['reject_reason']}"
    assert journal["status"] == "promoted"


def test_the_refusal_does_not_deny_the_training_that_happened(monkeypatch):
    """`registry_failed` is not `failed`.

    The model was trained, evaluated and written to disk. Reporting the run as
    failed would deny work that happened; reporting it as a success would claim
    a step that did not. The journal has to carry both facts at once.
    """
    result, journal = run_closed_loop(monkeypatch, {**GOOD_METRICS, "registry_ok": False})

    assert journal["status"] != "failed", "the training happened and must not be disowned"
    assert journal["metrics"]["new_auc"] == pytest.approx(0.9063)
    assert journal["metrics"]["promoted"] is False
    assert "registry_failed" in journal["metrics"]["reject_reason"]
    assert result["new_auc"] == pytest.approx(0.9063)


def test_offline_arrives_at_the_gate_as_not_registered(monkeypatch):
    """End to end over the seam: offline produces False, and False refuses promotion.

    This is the composition the two halves are for. `log_model_registry` is the
    real one, called offline, and its answer is what the gate reads -- so "we
    did not try" reaches the decision as "it is not registered" rather than
    quietly as success.
    """
    monkeypatch.setattr(mlflow_tracking, "_OFFLINE", True)
    registry_ok = mlflow_tracking.log_model_registry(object(), "RandomForest_retrain", {"auc": 0.9})
    assert registry_ok is False

    result, journal = run_closed_loop(
        monkeypatch, {**GOOD_METRICS, "registry_ok": bool(registry_ok)}
    )

    assert result["promoted"] is False
    assert "registry_failed" in (result["reject_reason"] or "")
    assert journal["status"] == "rejected"
