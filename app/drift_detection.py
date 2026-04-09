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
        if len(vals) == 0:
            stats_dict[col] = {
                "mean": None,
                "std": None,
                "min": None,
                "max": None,
                "median": None,
                "q25": None,
                "q75": None,
                "count": 0,
                "null_pct": round(float(df[col].isnull().mean() * 100), 2),
            }
            continue

        stats_dict[col] = {
            "mean": round(float(vals.mean()), 4),
            "std": round(float(vals.std()), 4) if len(vals) > 1 else 0.0,
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
        feature_cols = [
            c for c in reference_df.columns
            if c in current_df.columns and pd.api.types.is_numeric_dtype(reference_df[c])
        ]

    ref = reference_df[feature_cols].copy()
    cur = current_df[feature_cols].copy()

    ks = kolmogorov_smirnov_test(ref, cur)
    psi = population_stability_index(ref, cur)
    ref_stats = feature_statistics(ref)
    cur_stats = feature_statistics(cur)

    drifted_features = [
        f for f in feature_cols
        if ks.get(f, {}).get("drift") or psi.get(f, {}).get("drift")
    ]

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

    def __init__(self, window_size=100, min_samples=None, drift_threshold=2.0):
        self.window_size = int(window_size)
        self.min_samples = int(min_samples) if min_samples is not None else 10
        self.drift_threshold = float(drift_threshold)
        self._observations = []
        self._baseline = {}

    def set_baseline(self, baseline: dict):
        self._baseline = dict(baseline or {})

    def get_baseline(self):
        return dict(self._baseline)

    def add_observation(self, features: dict):
        self._observations.append(features)
        if len(self._observations) > self.window_size:
            self._observations = self._observations[-self.window_size:]

    @property
    def recent_data(self):
        return self._observations

    def get_observations(self):
        return self._observations

    def count(self):
        return len(self._observations)

    def _baseline_drift_check(self):
        total = len(self._observations)
        if total < self.min_samples:
            return {
                "status": "insufficient_data",
                "drift_detected": False,
                "drift_score": 0.0,
                "reason": "insufficient data",
                "observations": total,
                "required_min_samples": self.min_samples,
            }

        if not self._baseline:
            return {
                "status": "no_baseline",
                "drift_detected": False,
                "drift_score": 0.0,
                "reason": "baseline not set",
                "observations": total,
            }

        current_df = pd.DataFrame(self._observations)
        numeric_cols = [c for c in current_df.columns if pd.api.types.is_numeric_dtype(current_df[c])]
        feature_results = {}
        drifted = []

        for col in numeric_cols:
            mean_key = f"{col}_mean"
            std_key = f"{col}_std"
            if mean_key not in self._baseline:
                continue

            current_mean = float(current_df[col].dropna().mean()) if current_df[col].dropna().size else None
            baseline_mean = float(self._baseline[mean_key])
            baseline_std = float(self._baseline.get(std_key, 0.0) or 0.0)

            if current_mean is None:
                feature_results[col] = {
                    "drift": False,
                    "reason": "no numeric observations",
                }
                continue

            if baseline_std <= 0:
                z_score = abs(current_mean - baseline_mean)
            else:
                z_score = abs(current_mean - baseline_mean) / baseline_std

            drift = z_score >= self.drift_threshold
            if drift:
                drifted.append(col)

            drift_level = "HIGH" if z_score >= 3 else "MEDIUM" if drift else "LOW"

            feature_results[col] = {
                "baseline_mean": round(baseline_mean, 4),
                "baseline_std": round(baseline_std, 4),
                "current_mean": round(current_mean, 4),
                "z_score": round(float(z_score), 4),
                "drift": drift,
                "drift_level": drift_level,
                "severity": drift_level.lower(),
            }

        recent_alerts = []
        if drifted:
            for feature in drifted:
                fr = feature_results.get(feature, {})
                recent_alerts.append({
                    "feature": feature,
                    "drift_level": fr.get("drift_level", "MEDIUM"),
                    "message": f"Drift detected for {feature}",
                })

        return {
            "status": "drift_detected" if len(drifted) > 0 else "stable",
            "timestamp": datetime.utcnow().isoformat(),
            "observations": total,
            "drift_detected": len(drifted) > 0,
            "drift_score": round(len(drifted) / max(len(feature_results), 1), 2) if feature_results else 0.0,
            "drifted_features": drifted,
            "features": feature_results,
            "feature_results": feature_results,
            "recent_alerts": recent_alerts,
            "baseline_features": sorted(self._baseline.keys()),
        }

    def check_drift(self, reference_data=None, current_data=None, feature_cols=None):
        if reference_data is not None and current_data is not None:
            ref_df = pd.DataFrame(reference_data)
            cur_df = pd.DataFrame(current_data)
            if len(ref_df) < self.min_samples or len(cur_df) < self.min_samples:
                return {
                    "status": "insufficient_data",
                    "drift_detected": False,
                    "drift_score": 0.0,
                    "reason": "insufficient data",
                    "reference_samples": len(ref_df),
                    "current_samples": len(cur_df),
                }
            return run_drift_analysis(ref_df, cur_df, feature_cols=feature_cols)

        return self._baseline_drift_check()


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