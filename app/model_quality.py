"""Whether a measured AUC clears a threshold, allowing for the sample it came from.

The promotion gate compared point estimates: `new_auc >= 0.80` and
`new_auc - old_auc >= -0.02`. Both treat a number measured on 171 rows as
interchangeable with one measured on 3,415, and 0.81 against 0.80 on a small
test set is noise the comparison cannot see.

What this adds is the sample size. A lower confidence bound answers the question
the gate is actually asking -- "is this model at least as good as the
threshold" -- rather than "did this particular estimate land above it".

Hanley & McNeil's standard error for the area under the ROC curve is used
because it needs only the AUC and the class counts, which is all the retrain
records. A bootstrap over the predictions would be tighter and would require
storing them; that is a larger change and it is not what stands between the
gate and being able to tell noise from movement.

The bound is deliberately conservative: a model whose interval straddles the
threshold is refused. Under the old rule it was accepted.
"""
from __future__ import annotations

import math
from typing import Optional

#: 1.96 -> 95%. Named because the number appears in refusal messages and a
#: reader should be able to tell which interval was used.
Z_95 = 1.9599639845400545


def auc_standard_error(auc: float, n_pos: int, n_neg: int) -> Optional[float]:
    """Hanley & McNeil (1982), the exponential approximation.

    Returns None where the quantity is undefined -- one class absent, or a
    score outside [0, 1]. None is not zero: zero would claim a perfectly
    precise estimate, which is the opposite of what an absent class means.
    """
    if n_pos <= 0 or n_neg <= 0:
        return None
    if not (0.0 <= auc <= 1.0):
        return None

    q1 = auc / (2.0 - auc)
    q2 = 2.0 * auc * auc / (1.0 + auc)
    variance = (
        auc * (1.0 - auc)
        + (n_pos - 1) * (q1 - auc * auc)
        + (n_neg - 1) * (q2 - auc * auc)
    ) / (n_pos * n_neg)
    return math.sqrt(variance) if variance > 0 else 0.0


def auc_lower_bound(auc: float, n_pos: int, n_neg: int,
                    z: float = Z_95) -> Optional[float]:
    """The bottom of the confidence interval, clipped at zero.

    Clipped rather than allowed negative: an AUC cannot be below zero, and a
    bound that reports -0.03 invites the reader to treat the arithmetic as the
    measurement.
    """
    se = auc_standard_error(auc, n_pos, n_neg)
    if se is None:
        return None
    return max(0.0, auc - z * se)


def clears_threshold(auc: Optional[float], n_pos: int, n_neg: int,
                     threshold: float, z: float = Z_95):
    """(passed, reason). `reason` is None when it passed.

    Refuses when the bound cannot be computed. "We could not tell" and "it
    passed" are different answers and only one of them is a promotion -- the
    same distinction the deployment guard makes when it cannot read a route
    table.
    """
    if auc is None:
        return False, "no AUC was recorded for this run"

    bound = auc_lower_bound(float(auc), n_pos, n_neg, z=z)
    if bound is None:
        return False, (
            f"AUC {float(auc):.4f} cannot be bounded: the test set has "
            f"{n_pos} positive and {n_neg} negative rows, and a confidence "
            f"interval needs both classes"
        )
    if bound < threshold:
        return False, (
            f"AUC {float(auc):.4f} does not clear {threshold} once the sample "
            f"is allowed for: 95% lower bound {bound:.4f} on "
            f"{n_pos + n_neg} test rows"
        )
    return True, None
