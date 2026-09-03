"""One promotion gate, exercised rather than described (roadmap phase 5).

Two paths promoted models and disagreed. `app/scheduler.py`'s closed loop asked
three questions; `POST /mlops/auto-retrain` in `app/api/infra.py` asked one of
them. Not a different policy — a strict subset:

| refusal                                   | scheduler | infra |
|-------------------------------------------|-----------|-------|
| below the absolute floor (lower bound)     | yes       | no    |
| not registered in MLflow (#189)            | yes       | no    |
| materially worse than what is serving      | yes       | yes   |

So a candidate at AUC 0.55 against a champion at 0.56 was refused by one and
promoted by the other, and which happened depended on who triggered the run.

These are behavioural: they call the gate. `tests/test_registration_gates_promotion.py`
had to asserted its contract by grepping `scheduler.py`, and said so in its own
header — the mlflow client connects before a monkeypatch can take effect, so the
cases spent four minutes failing with ConnectionRefused instead of testing
anything. A decision function that takes two dicts has no client to isolate.
"""
import pytest

from app.promotion import (MAX_AUC_REGRESSION, MIN_AUC_THRESHOLD,
                           evaluate_promotion)


def metrics(auc, *, n_pos=None, n_neg=None, registry_ok=None):
    """A metrics dict shaped like the one `_do_retrain` returns."""
    out = {"auc_roc": auc}
    if n_pos is not None:
        out["test_positive"] = n_pos
    if n_neg is not None:
        out["test_negative"] = n_neg
    if registry_ok is not None:
        out["registry_ok"] = registry_ok
    return out


# --- the absolute floor ----------------------------------------------------

def test_a_model_below_the_floor_is_refused():
    decision = evaluate_promotion(metrics(0.55))
    assert not decision.promoted
    assert "below minimum threshold" in decision.reject_reason


def test_a_model_above_the_floor_is_promoted():
    assert evaluate_promotion(metrics(0.91)).promoted


def test_the_floor_is_the_lower_bound_when_the_sample_is_known():
    """The same point estimate, two sample sizes, two verdicts.

    AUC 0.85 clears 0.80 as a number either way. Measured against
    `app/model_quality.auc_lower_bound`:

        0.85 on   171 rows -> 95% lower bound 0.7826  -> refused
        0.85 on   600 rows -> 95% lower bound 0.8141  -> promoted

    The estimate is not the claim; the interval is. That is what
    `app/model_quality.py` exists for, and why the floor is applied to the bound.
    """
    small = evaluate_promotion(metrics(0.85, n_pos=57, n_neg=114))
    assert not small.promoted, (
        "0.85 on 171 rows was accepted as clearing 0.80 while its 95% lower "
        "bound is 0.78")
    assert "lower bound" in small.reject_reason

    large = evaluate_promotion(metrics(0.85, n_pos=200, n_neg=400))
    assert large.promoted, (
        "the same estimate on 600 rows is a stronger claim and must pass")


def test_without_class_counts_the_point_estimate_is_used():
    """Rows recorded before the bound existed carry no `test_positive`. Judging
    them by the rule in force when they were written beats refusing them for
    lacking a field they could not have had."""
    assert evaluate_promotion(metrics(0.85)).promoted
    assert not evaluate_promotion(metrics(0.79)).promoted


# --- the registry ----------------------------------------------------------

def test_an_unregistered_model_is_refused():
    decision = evaluate_promotion(metrics(0.95, registry_ok=False))
    assert not decision.promoted
    assert "registry_failed" in decision.reject_reason


def test_an_absent_registry_flag_is_not_a_refusal():
    """`is False`, not falsy: None means unknown, and old rows have no flag."""
    assert evaluate_promotion(metrics(0.95)).promoted
    assert evaluate_promotion(metrics(0.95, registry_ok=True)).promoted


def test_the_refusal_names_registry_failure_not_a_failed_run():
    """The model was trained, evaluated and written. Calling the run failed
    would deny work that happened."""
    reason = evaluate_promotion(metrics(0.95, registry_ok=False)).reject_reason
    assert reason.startswith("registry_failed")
    assert "trained and written" in reason


# --- degradation -----------------------------------------------------------

def test_a_materially_worse_model_is_refused():
    decision = evaluate_promotion(metrics(0.85), old_auc=0.90)
    assert not decision.promoted
    assert "AUC degraded" in decision.reject_reason


def test_a_slightly_worse_model_still_passes():
    """The rule is a tolerance, not a ratchet: within it, a step down is
    accepted. Asserting the boundary keeps that deliberate."""
    assert evaluate_promotion(metrics(0.89), old_auc=0.90).promoted
    assert not evaluate_promotion(
        metrics(0.90 - MAX_AUC_REGRESSION - 0.001), old_auc=0.90).promoted


def test_a_first_model_is_not_refused_for_having_no_predecessor():
    assert evaluate_promotion(metrics(0.95), old_auc=None).promoted


# --- the refusals are independent -----------------------------------------

def test_clearing_the_floor_does_not_skip_the_later_checks():
    """The regression this shape prevents. The checks were once an if/elif
    chain, and putting the bound at the front silently disabled the rest: a
    model that cleared it never reached them."""
    decision = evaluate_promotion(metrics(0.95, registry_ok=False), old_auc=0.90)
    assert not decision.promoted, (
        "a model that cleared the absolute floor skipped the registry check")


def test_the_first_refusal_is_the_one_reported():
    """One outcome per run, so the counters stay a count of runs."""
    decision = evaluate_promotion(metrics(0.20, registry_ok=False), old_auc=0.90)
    assert not decision.promoted
    assert "below minimum threshold" in decision.reject_reason


def test_a_promoted_decision_carries_no_reason():
    decision = evaluate_promotion(metrics(0.95))
    assert decision.promoted and decision.reject_reason is None


# --- the divergence itself -------------------------------------------------

def test_the_case_that_the_two_paths_answered_differently():
    """The concrete defect. `POST /mlops/auto-retrain` promoted this; the
    scheduler refused it."""
    decision = evaluate_promotion(metrics(0.55), old_auc=0.56)
    assert not decision.promoted, (
        "a model at 0.55 was promoted because it was only 0.01 worse than the "
        "champion, with nothing asking whether 0.55 is good enough at all")
    assert "below minimum threshold" in decision.reject_reason


def test_neither_call_site_still_decides_for_itself():
    """Structural, and deliberately so: the behavioural tests above cannot see a
    *third* copy of the rule appearing somewhere else.

    Read from the source because that is the only place the property lives — one
    definition — and it is the property phase 5 asks for.
    """
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for relative in ("app/scheduler.py", "app/api/infra.py"):
        with open(os.path.join(root, relative), encoding="utf-8") as handle:
            body = handle.read()

        assert "evaluate_promotion(" in body, (
            f"{relative} does not call the shared gate")
        # The literals that were the duplicated rule. Comments explaining the
        # history are fine; a live comparison is not.
        code = "\n".join(
            line for line in body.splitlines()
            if not line.lstrip().startswith("#"))
        assert "auc_delta < -0.02" not in code, (
            f"{relative} still compares the degradation tolerance itself")
        assert "MIN_AUC_THRESHOLD = " not in code, (
            f"{relative} defines its own floor; there must be one definition")


def test_the_thresholds_have_one_definition():
    assert MIN_AUC_THRESHOLD == 0.80
    assert MAX_AUC_REGRESSION == 0.02
