"""A candidate whose AUC could not be measured does not clear the gate.

`evaluate_promotion` started at `promoted = True` and every one of its three
refusals required the candidate's AUC to be known:

    if new_auc is not None and n_pos is not None and n_neg is not None: ...
    elif new_auc is not None and float(new_auc) < MIN_AUC_THRESHOLD: ...
    if promoted and registry_ok is False: ...
    if promoted and old_auc is not None and new_auc is not None: ...

So `roc_auc = None` silenced all of them and the candidate was promoted --
measured 2026-09-25 against a champion at 0.905.

That state is on the live path, not hypothetical. `app/api/retrain.py`:

    try:
        auc = round(roc_auc_score(y_test, y_proba), 4)
    except Exception:
        auc = None

and `new_metrics` carries that `None` straight into `gated_decision`, whose
`promoted` branch calls `activate_promoted_candidate`. The closed loop's own
log line already spelled the state -- `"unrecorded"` when `new_auc is None` --
so the absence was seen and was not made a refusal.

This is the lesson the repository records for the other check in the same loop:
"A check that could not run is **not** 'no drift'." An AUC that could not be
computed is not an AUC that passed.

Two shapes are pinned here, because they fail for different reasons:

- **absent or unreadable** -- `None`, `{}`, no AUC key at all;
- **zero read as absent** -- `_auc_of` was `metrics.get("auc_roc") or
  metrics.get("roc_auc")`, and `0.0` is falsy, so an AUC of exactly zero became
  `None` and was promoted. Only the `auc_roc` spelling could reach that today
  (the retrain writes `roc_auc`), which makes it a trap rather than a live
  defect -- and a trap worth closing while the line is being touched.

What is deliberately *not* changed: an absent **champion** AUC (`old_auc is
None`) still does not refuse. A first model has no predecessor, and
`gated_decision` already refuses separately when the champion exists but its
score cannot be read (#329).
"""
import pytest

from app.promotion import MIN_AUC_THRESHOLD, _auc_of, evaluate_promotion

CHAMPION = 0.905

#: The shape `_do_retrain` builds, with the AUC filled in by the caller.
def _retrain_metrics(**over):
    m = {
        "accuracy": 0.86, "f1_score": 0.9, "best_f1": 0.905,
        "roc_auc": 0.95, "best_threshold": 0.5,
        "train_samples": 3415, "test_samples": 854,
        "split_kind": "temporal",
        "test_positive": 400, "test_negative": 454,
        "registry_ok": True,
    }
    m.update(over)
    return m


def test_the_fixture_is_promoted_when_the_auc_is_good():
    """The denominator. Without this the refusals below could pass on a
    fixture that was rejected for some unrelated reason."""
    decision = evaluate_promotion(_retrain_metrics(), old_auc=CHAMPION)
    assert decision.promoted, decision.reject_reason


@pytest.mark.parametrize("metrics,label", [
    (_retrain_metrics(roc_auc=None), "roc_auc=None, the retrain's except branch"),
    ({}, "no metrics at all"),
    (None, "None instead of metrics"),
    ({"accuracy": 0.5, "f1_score": 0.4}, "metrics without any AUC key"),
])
def test_a_candidate_without_a_measured_auc_is_refused(metrics, label):
    decision = evaluate_promotion(metrics, old_auc=CHAMPION)

    assert not decision.promoted, (
        f"a candidate with {label} cleared the gate against a champion at "
        f"{CHAMPION}. Every refusal requires the AUC to be known, so an "
        f"unmeasured one silences all of them."
    )
    assert "auc" in (decision.reject_reason or "").lower(), decision.reject_reason


@pytest.mark.parametrize("spelling", ["roc_auc", "auc_roc"])
def test_an_auc_of_exactly_zero_is_a_measurement_not_an_absence(spelling):
    """`x or y` reads 0.0 as missing. Both spellings must refuse."""
    assert _auc_of({spelling: 0.0}) == 0.0, (
        f"_auc_of read {spelling}=0.0 as absent; zero is a measurement, and the "
        f"worst one there is"
    )

    metrics = _retrain_metrics()
    metrics.pop("roc_auc")          # only the spelling under test carries a value
    metrics[spelling] = 0.0
    decision = evaluate_promotion(metrics, old_auc=CHAMPION)
    assert not decision.promoted, f"AUC 0.0 under {spelling} cleared the gate"


def test_a_first_model_is_still_not_refused_for_having_no_predecessor():
    """The change must not tighten the *champion* side: `old_auc=None` is a
    first model, and that was never a refusal."""
    decision = evaluate_promotion(_retrain_metrics(), old_auc=None)

    assert decision.promoted, decision.reject_reason


def test_a_bad_auc_is_still_refused_for_being_bad():
    """The existing refusal keeps its own reason, rather than being swallowed
    by the new one."""
    decision = evaluate_promotion(_retrain_metrics(roc_auc=0.30), old_auc=CHAMPION)

    assert not decision.promoted
    assert str(MIN_AUC_THRESHOLD) in (decision.reject_reason or ""), decision.reject_reason
