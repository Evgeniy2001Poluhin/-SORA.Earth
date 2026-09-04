"""Whether a retrained model may become the champion — one definition (phase 5).

Two paths promoted models, and they did not agree. `app/scheduler.py` refused a
model on three independent grounds; `app/api/infra.py`, reached through
`POST /mlops/auto-retrain`, applied one of the three. It was not a different
policy, it was a strict subset — the same degradation rule with no floor under
it and no requirement that the model be findable.

The consequence was not subtle. A model measuring AUC 0.55 against a champion at
0.56 is refused by the scheduler, which wants 0.80, and promoted by the admin
endpoint, which only asks whether it got materially worse. Which rule applied
depended on which caller happened to trigger the retrain, and nothing in either
place said the other existed.

So the decision moves here, and both call sites ask this module. The rules are
unchanged: this is what the scheduler already did, in one place, with the second
caller no longer allowed to invent its own answer.

## Why a pure function

The gate used to live inside a job that reaches MLflow and a database. That made
it untestable in practice, and `tests/test_registration_gates_promotion.py` says
so in its own header: the behavioural cases were left unwritten because the
mlflow client connects before a monkeypatch can take effect, so they spent four
minutes failing with `ConnectionRefused` instead of testing the decision. The
contract was asserted by grepping the source of `scheduler.py` — which cannot
observe a second caller, and which breaks on a refactor that changes no
behaviour at all.

Taking metrics in and returning a verdict removes that. There is no client, no
session and no network here, so the refusals can be exercised directly.

## Three refusals, asked in sequence

They are separate questions and each is asked in turn, guarded on the decision
so far, rather than chained with `elif`. That shape is deliberate and predates
this module: the checks were once an if/elif chain, and putting the absolute
bound at the front silently disabled everything behind it, because a model that
cleared the bound never reached the rest.
"""
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from app.model_quality import clears_threshold

#: The floor a model must clear on its own, regardless of what preceded it.
MIN_AUC_THRESHOLD = 0.80

#: How much worse than the serving model a candidate may be before it is
#: refused. A model can clear the absolute bar and still be a step down, which
#: is a separate question from whether it is good enough in isolation.
MAX_AUC_REGRESSION = 0.02


@dataclass(frozen=True)
class PromotionDecision:
    """The verdict, and why — never a bare boolean.

    `reject_reason` is the operator-facing sentence recorded against the run and
    logged. It is `None` exactly when `promoted` is True.
    """

    promoted: bool
    reject_reason: Optional[str] = None


def _auc_of(metrics: Mapping[str, Any]) -> Optional[float]:
    """Both spellings appear in stored rows, so both are read."""
    return metrics.get("auc_roc") or metrics.get("roc_auc")


def evaluate_promotion(
    new_metrics: Optional[Mapping[str, Any]],
    old_auc: Optional[float] = None,
) -> PromotionDecision:
    """Decide whether the candidate described by `new_metrics` may be promoted.

    `old_auc` is the AUC of the model currently serving, or None when there is
    nothing to compare against — a first model is not refused for having no
    predecessor.
    """
    metrics = new_metrics if isinstance(new_metrics, Mapping) else {}
    new_auc = _auc_of(metrics)

    promoted = True
    reject_reason: Optional[str] = None

    def _reject(reason: str) -> None:
        nonlocal promoted, reject_reason
        promoted = False
        reject_reason = reason

    # 1. Absolute quality, on the lower bound rather than the estimate.
    #
    # `new_auc >= 0.80` treats a number measured on 171 rows as interchangeable
    # with one measured on 3,415. Measured: AUC 0.81 on 171 rows has a 95% lower
    # bound of 0.746, so it was accepted as clearing 0.80 while being
    # indistinguishable from 0.75.
    #
    # The point estimate is used only when the class counts are absent -- runs
    # recorded before that change carry no `test_positive` -- so an old row is
    # judged by the rule in force when it was written rather than refused for
    # lacking a field it could not have had.
    n_pos = metrics.get("test_positive")
    n_neg = metrics.get("test_negative")
    if new_auc is not None and n_pos is not None and n_neg is not None:
        cleared, why = clears_threshold(
            new_auc, int(n_pos), int(n_neg), MIN_AUC_THRESHOLD)
        if not cleared:
            _reject(why)
    elif new_auc is not None and float(new_auc) < MIN_AUC_THRESHOLD:
        _reject(f"AUC below minimum threshold: {new_auc:.4f} < {MIN_AUC_THRESHOLD}")

    # 2. Registered, or not promoted (#189).
    #
    # A model that exists only on one container's disk is not a model the
    # platform can point at: the registry is where a version is identified,
    # compared and rolled back from.
    #
    # Absent means unknown, and unknown is not a refusal -- runs recorded before
    # this carry no `registry_ok`. Hence `is False`, not falsy.
    registry_ok = metrics.get("registry_ok")
    if promoted and registry_ok is False:
        _reject("registry_failed: the model was trained and written but not "
                "registered in MLflow, so it cannot be identified or rolled "
                "back from")

    # 3. Not worse than what is already serving.
    if promoted and old_auc is not None and new_auc is not None:
        auc_delta = float(new_auc) - float(old_auc)
        if auc_delta < -MAX_AUC_REGRESSION:
            _reject("AUC degraded: %.4f -> %.4f (delta=%+.4f)"
                    % (float(old_auc), float(new_auc), auc_delta))

    return PromotionDecision(promoted=promoted, reject_reason=reject_reason)
