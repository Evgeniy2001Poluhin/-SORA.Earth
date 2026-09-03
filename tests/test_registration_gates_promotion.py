"""An unregistered model is not promoted, and says which step failed.

#189. `log_model_registry` returned None whether it worked or not, so a caller
could not tell a registered model from one whose upload was refused. Measured
on production 2026-08-15: registration had been failing with
`PermissionError: '/mlflow'` while every retrain reported success -- experiments
0 and 1 carry an absolute `artifact_location` from before the server ran with
`--serve-artifacts`, and the client tried to write that path locally.

A model that exists only on one container's disk is not one the platform can
point at. The registry is where a version is identified, compared and rolled
back from.

## These used to read the source, and no longer have to

The original file asserted this contract by grepping `app/scheduler.py`, and its
header explained why: the decision was embedded in a job that reaches MLflow and
a database, the mlflow client connects before a monkeypatch can take effect, and
the behavioural cases spent four minutes failing with ConnectionRefused rather
than testing anything.

Phase 5 moved the decision into `app/promotion.py` to give the two callers one
answer, and a pure function has no client to isolate. The same three claims are
made below by calling it. Two consequences worth stating: they now cover
`POST /mlops/auto-retrain` as well, which the source-reading version could not
see at all; and they no longer break when code moves between files without
changing behaviour.

The two cases the header named as still missing -- "a refused upload returns
False" and "offline returns False" -- are about `log_model_registry`, not about
the gate, and remain unwritten. Named again rather than quietly dropped.
"""
from app.promotion import evaluate_promotion


def test_the_gate_refuses_an_unregistered_model():
    decision = evaluate_promotion({"auc_roc": 0.95, "registry_ok": False})

    assert not decision.promoted, "a model that was never registered was promoted"
    assert "registry_failed" in decision.reject_reason


def test_an_absent_flag_is_not_a_refusal():
    """Runs recorded before this carry no `registry_ok`. Refusing them would be
    judging old rows by a field they could not have had -- the same reasoning
    as the confidence bound's fallback.

    `is False`, not falsy: `None` must not trigger the refusal.
    """
    assert evaluate_promotion({"auc_roc": 0.95}).promoted
    assert evaluate_promotion({"auc_roc": 0.95, "registry_ok": True}).promoted


def test_it_is_reported_as_registry_failed_not_as_a_failed_run():
    """The model was trained, evaluated and written. Calling the run failed
    would deny work that happened; calling it success would claim a step that
    did not."""
    reason = evaluate_promotion(
        {"auc_roc": 0.95, "registry_ok": False}).reject_reason

    assert reason.startswith("registry_failed: ")
    assert "trained and written" in reason
    assert not reason.startswith("failed")
