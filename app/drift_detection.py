"""Data drift detection using KS-test and PSI."""
import logging
import numpy as np
import pandas as pd
from scipy import stats
from datetime import datetime

logger = logging.getLogger(__name__)


def kolmogorov_smirnov_test(reference, current, alpha=0.05):
    """KS test for each feature. Returns drift info per feature."""
    results = {}
    for col in reference.columns:
        ref = reference[col].dropna().values
        cur = current[col].dropna().values
        if len(ref) < 5 or len(cur) < 5:
            results[col] = {"statistic": None, "p_value": None, "drift": False, "reason": "insufficient data"}
            continue
        stat, p_val = stats.ks_2samp(ref, cur)
        results[col] = {
            "statistic": round(float(stat), 4),
            "p_value": round(float(p_val), 6),
            "drift": p_val < alpha,
            "severity": "high" if p_val < 0.001 else "medium" if p_val < alpha else "none",
        }
    return results


def population_stability_index(reference, current, bins=10):
    """PSI for each feature. PSI > 0.2 = significant drift."""
    results = {}
    for col in reference.columns:
        ref = reference[col].dropna().values
        cur = current[col].dropna().values
        if len(ref) < 10 or len(cur) < 10:
            results[col] = {"psi": None, "drift": False, "reason": "insufficient data"}
            continue

        breakpoints = np.percentile(ref, np.linspace(0, 100, bins + 1))
        breakpoints = np.unique(breakpoints)
        if len(breakpoints) < 3:
            breakpoints = np.linspace(ref.min() - 1, ref.max() + 1, bins + 1)

        ref_hist = np.histogram(ref, bins=breakpoints)[0] / len(ref)
        cur_hist = np.histogram(cur, bins=breakpoints)[0] / len(cur)

        ref_hist = np.clip(ref_hist, 1e-6, None)
        cur_hist = np.clip(cur_hist, 1e-6, None)

        psi = float(np.sum((cur_hist - ref_hist) * np.log(cur_hist / ref_hist)))
        results[col] = {
            "psi": round(psi, 4),
            "drift": psi > 0.2,
            "severity": "high" if psi > 0.25 else "medium" if psi > 0.1 else "none",
        }
    return results


def feature_statistics(df):
    """Basic stats for a feature set."""
    stats_dict = {}
    for col in df.columns:
        vals = df[col].dropna()
        stats_dict[col] = {
            "mean": round(float(vals.mean()), 4),
            "std": round(float(vals.std()), 4),
            "min": round(float(vals.min()), 4),
            "max": round(float(vals.max()), 4),
            "median": round(float(vals.median()), 4),
            "q25": round(float(vals.quantile(0.25)), 4),
            "q75": round(float(vals.quantile(0.75)), 4),
            "count": int(len(vals)),
            "null_pct": round(float(df[col].isnull().mean() * 100), 2),
        }
    return stats_dict


def run_drift_analysis(reference_df, current_df, feature_cols=None):
    """Full drift analysis: KS + PSI + stats comparison."""
    if feature_cols is None:
        feature_cols = [c for c in reference_df.columns if c in current_df.columns
                        and reference_df[c].dtype in ('float64', 'int64', 'float32', 'int32')]

    ref = reference_df[feature_cols].copy()
    cur = current_df[feature_cols].copy()

    ks = kolmogorov_smirnov_test(ref, cur)
    psi = population_stability_index(ref, cur)
    ref_stats = feature_statistics(ref)
    cur_stats = feature_statistics(cur)

    drifted_features = [f for f in feature_cols if ks.get(f, {}).get("drift") or psi.get(f, {}).get("drift")]

    result = {
        "timestamp": datetime.utcnow().isoformat(),
        "reference_samples": len(ref),
        "current_samples": len(cur),
        "features_analyzed": feature_cols,
        "ks_test": ks,
        "psi": psi,
        "reference_stats": ref_stats,
        "current_stats": cur_stats,
        "drifted_features": drifted_features,
        "drift_detected": len(drifted_features) > 0,
        "drift_score": round(len(drifted_features) / max(len(feature_cols), 1), 2),
    }
    return _sanitize(result)


class DriftDetector:
    """Simple observation collector for real-time drift tracking."""
    def __init__(self):
        self._observations = []

    def add_observation(self, features: dict):
        self._observations.append(features)
        if len(self._observations) > 10000:
            self._observations = self._observations[-5000:]

    def get_observations(self):
        return self._observations

    def count(self):
        return len(self._observations)


drift_detector = DriftDetector()


def _sanitize(obj):
    """Convert numpy types to native Python for JSON serialization."""
    import numpy as np
    if isinstance(obj, dict):
        return {_sanitize(k): _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(i) for i in obj]
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    return obj
