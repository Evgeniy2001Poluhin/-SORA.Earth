import numpy as np
from collections import deque
from datetime import datetime

class DriftDetector:
    def __init__(self, window_size=100):
        self.window_size = window_size
        self.baseline_stats = {}
        self.recent_data = deque(maxlen=window_size)
        self.alerts = []

    def set_baseline(self, feature_stats: dict):
        self.baseline_stats = feature_stats

    def add_observation(self, features: dict):
        self.recent_data.append(features)

    def check_drift(self):
        if len(self.recent_data) < 10:
            return {"status": "insufficient_data", "observations": len(self.recent_data)}

        results = {}
        data = list(self.recent_data)

        for feature in ["budget", "co2_reduction", "social_impact", "duration_months"]:
            values = [d.get(feature, 0) for d in data if feature in d]
            if not values:
                continue
            current_mean = np.mean(values)
            current_std = np.std(values)
            baseline_mean = self.baseline_stats.get(f"{feature}_mean", current_mean)
            baseline_std = self.baseline_stats.get(f"{feature}_std", current_std) or 1

            z_score = abs(current_mean - baseline_mean) / baseline_std
            drift = "HIGH" if z_score > 3 else "MEDIUM" if z_score > 2 else "LOW"

            results[feature] = {
                "baseline_mean": round(baseline_mean, 2),
                "current_mean": round(current_mean, 2),
                "z_score": round(z_score, 2),
                "drift_level": drift
            }

            if drift == "HIGH":
                self.alerts.append({
                    "feature": feature,
                    "z_score": round(z_score, 2),
                    "timestamp": datetime.utcnow().isoformat()
                })

        has_drift = any(r["drift_level"] == "HIGH" for r in results.values())
        return {
            "status": "drift_detected" if has_drift else "stable",
            "observations": len(self.recent_data),
            "features": results,
            "recent_alerts": self.alerts[-5:]
        }

drift_detector = DriftDetector()
drift_detector.set_baseline({
    "budget_mean": 75000, "budget_std": 50000,
    "co2_reduction_mean": 45, "co2_reduction_std": 25,
    "social_impact_mean": 5.5, "social_impact_std": 2.5,
    "duration_months_mean": 18, "duration_months_std": 12,
})
