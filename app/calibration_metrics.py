"""Calibration metrics for binary classifier evaluation.

Pure functions, no I/O. Used by HTTP endpoints and unit tests.

References: Brier (1950), Murphy (1973), Naeini et al. (2015).
"""
from __future__ import annotations
from typing import Dict, List, Sequence


def _validate(probs, labels):
    if len(probs) != len(labels):
        raise ValueError("length mismatch: probs=" + str(len(probs)) + " labels=" + str(len(labels)))
    if len(probs) == 0:
        raise ValueError("empty inputs")
    for p in probs:
        if not (0.0 <= float(p) <= 1.0):
            raise ValueError("probability out of [0,1]: " + str(p))
    for y in labels:
        if int(y) not in (0, 1):
            raise ValueError("label not binary: " + str(y))


def brier_score(probs, labels):
    _validate(probs, labels)
    n = len(probs)
    total = 0.0
    for p, y in zip(probs, labels):
        diff = float(p) - float(y)
        total += diff * diff
    return total / n


def reliability_curve(probs, labels, n_bins=10):
    _validate(probs, labels)
    if n_bins < 2:
        raise ValueError("n_bins must be >= 2")
    width = 1.0 / n_bins
    sums_p = [0.0] * n_bins
    sums_y = [0.0] * n_bins
    counts = [0] * n_bins
    for p, y in zip(probs, labels):
        idx = min(int(float(p) / width), n_bins - 1)
        sums_p[idx] += float(p)
        sums_y[idx] += float(y)
        counts[idx] += 1
    bin_lower, bin_upper, mean_pred, mean_obs = [], [], [], []
    for i in range(n_bins):
        bin_lower.append(round(i * width, 4))
        bin_upper.append(round((i + 1) * width, 4))
        if counts[i] > 0:
            mean_pred.append(round(sums_p[i] / counts[i], 4))
            mean_obs.append(round(sums_y[i] / counts[i], 4))
        else:
            mean_pred.append(None)
            mean_obs.append(None)
    return {
        "bin_lower": bin_lower,
        "bin_upper": bin_upper,
        "mean_predicted": mean_pred,
        "mean_observed": mean_obs,
        "count": counts,
    }


def expected_calibration_error(probs, labels, n_bins=10):
    """Weighted mean absolute gap between predicted and observed per bin."""
    curve = reliability_curve(probs, labels, n_bins=n_bins)
    n = sum(curve["count"])
    if n == 0:
        return 0.0
    ece = 0.0
    for i in range(n_bins):
        c = curve["count"][i]
        if c == 0:
            continue
        gap = abs(curve["mean_predicted"][i] - curve["mean_observed"][i])
        ece += (c / n) * gap
    return round(ece, 6)


def murphy_decomposition(probs, labels, n_bins=10):
    """Brier = Reliability - Resolution + Uncertainty."""
    _validate(probs, labels)
    n = len(probs)
    base_rate = sum(int(y) for y in labels) / n
    uncertainty = base_rate * (1.0 - base_rate)
    curve = reliability_curve(probs, labels, n_bins=n_bins)
    reliability = 0.0
    resolution = 0.0
    for i in range(n_bins):
        c = curve["count"][i]
        if c == 0:
            continue
        mp = curve["mean_predicted"][i]
        mo = curve["mean_observed"][i]
        w = c / n
        reliability += w * (mp - mo) ** 2
        resolution += w * (mo - base_rate) ** 2
    brier = brier_score(probs, labels)
    return {
        "brier": round(brier, 6),
        "reliability": round(reliability, 6),
        "resolution": round(resolution, 6),
        "uncertainty": round(uncertainty, 6),
        "base_rate": round(base_rate, 6),
        "n_samples": n,
    }
