"""PSI + KS statistical drift metrics.

PSI thresholds (Siddiqi 2006, credit scoring industry standard):
  < 0.10       stable
  0.10 - 0.25  warning
  >= 0.25      drift
"""
from __future__ import annotations
import numpy as np
from scipy.stats import ks_2samp


def psi(ref_counts: np.ndarray, live_counts: np.ndarray, eps: float = 1e-6) -> float:
    ref = np.asarray(ref_counts, dtype=float)
    live = np.asarray(live_counts, dtype=float)
    ref_p = ref / max(ref.sum(), eps)
    live_p = live / max(live.sum(), eps)
    ref_p = np.clip(ref_p, eps, None)
    live_p = np.clip(live_p, eps, None)
    return float(np.sum((live_p - ref_p) * np.log(live_p / ref_p)))


def status_from_psi(value: float) -> str:
    if value < 0.10:
        return "stable"
    if value < 0.25:
        return "warning"
    return "drift"


def ks(ref_vals: np.ndarray, live_vals: np.ndarray) -> tuple[float, float]:
    if len(ref_vals) < 2 or len(live_vals) < 2:
        return 0.0, 1.0
    stat, pvalue = ks_2samp(ref_vals, live_vals)
    return float(stat), float(pvalue)


def histogram(values: np.ndarray, bin_edges: np.ndarray) -> np.ndarray:
    counts, _ = np.histogram(values, bins=bin_edges)
    return counts.astype(int)


def category_counts(values, categories: list[str]) -> np.ndarray:
    import collections
    c = collections.Counter(values)
    return np.array([c.get(cat, 0) for cat in categories], dtype=int)
