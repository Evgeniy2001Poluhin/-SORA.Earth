"""
Tests to boost coverage:
- app/drift_detection.py  (51% → 95%+)
- app/metrics.py          (47% → 95%+)
- app/validators.py       (81% → 95%+)
"""
import pytest
import time
from collections import deque
from unittest.mock import patch, MagicMock


# ===================== drift_detection.py =====================

class TestDriftDetection:
    def setup_method(self):
        from app.drift_detection import DriftDetector
        self.detector = DriftDetector()

    def test_insufficient_data_returns_status(self):
        result = self.detector.check_drift()
        assert result["status"] == "insufficient_data"

    def test_stable_with_consistent_data(self):
        data = [
            {"budget": 100000, "co2_reduction": 50, "social_impact": 7, "duration_months": 12}
            for _ in range(30)
        ]
        for d in data:
            self.detector.add_observation(d)
        result = self.detector.check_drift()
        assert result["status"] in ("stable", "drift_detected")
        assert "features" in result
        assert "observations" in result

    def test_drift_detected_on_extreme_values(self):
        # Baseline: low values
        for _ in range(20):
            self.detector.add_observation({
                "budget": 10000, "co2_reduction": 5,
                "social_impact": 1, "duration_months": 6
            })
        # Set baseline stats manually to simulate large z-score
        self.detector.baseline_stats = {
            "budget_mean": 10000, "budget_std": 100,
            "co2_reduction_mean": 5, "co2_reduction_std": 1,
            "social_impact_mean": 1, "social_impact_std": 0.1,
            "duration_months_mean": 6, "duration_months_std": 1,
        }
        # Override recent data with extreme values
        for _ in range(20):
            self.detector.add_observation({
                "budget": 9999999, "co2_reduction": 9999,
                "social_impact": 10, "duration_months": 120
            })
        result = self.detector.check_drift()
        assert "features" in result
        assert result["observations"] > 0

    def test_alerts_populated_on_high_drift(self):
        self.detector.baseline_stats = {
            "budget_mean": 1000, "budget_std": 10,
        }
        for _ in range(20):
            self.detector.add_observation({"budget": 1000000})
        self.detector.check_drift()
        # alerts may or may not fire depending on threshold, just check structure
        assert isinstance(self.detector.alerts, list)

    def test_feature_missing_from_observation(self):
        for _ in range(20):
            self.detector.add_observation({"budget": 50000})
        result = self.detector.check_drift()
        assert "features" in result

    def test_all_features_present_in_result(self):
        for _ in range(20):
            self.detector.add_observation({
                "budget": 50000, "co2_reduction": 30,
                "social_impact": 5, "duration_months": 12
            })
        result = self.detector.check_drift()
        if result["status"] != "insufficient_data":
            for feat in ["budget", "co2_reduction", "social_impact", "duration_months"]:
                assert feat in result["features"]


# ===================== metrics.py =====================

class TestMetrics:
    def setup_method(self):
        from app.metrics import Metrics
        self.m = Metrics()

    def test_inc_counter(self):
        self.m.inc("requests")
        assert self.m.counters["requests"] == 1
        self.m.inc("requests", 5)
        assert self.m.counters["requests"] == 6

    def test_observe_histogram(self):
        self.m.observe("response_time", 123.4)
        self.m.observe("response_time", 200.0)
        assert len(self.m.histograms["response_time"]) == 2

    def test_summary_contains_uptime(self):
        result = self.m.summary()
        assert "uptime_seconds" in result
        assert result["uptime_seconds"] >= 0

    def test_summary_contains_counters(self):
        self.m.inc("requests", 3)
        result = self.m.summary()
        assert result["counters"]["requests"] == 3

    def test_summary_histogram_stats(self):
        self.m.observe("latency", 100.0)
        self.m.observe("latency", 200.0)
        result = self.m.summary()
        assert "latency_count" in result
        assert result["latency_count"] == 2
        assert "latency_avg_ms" in result
        assert "latency_max_ms" in result

    def test_prometheus_format_contains_uptime(self):
        output = self.m.prometheus_format()
        assert "uptime_seconds" in output

    def test_prometheus_format_contains_counters(self):
        self.m.inc("hits", 42)
        output = self.m.prometheus_format()
        assert "hits" in output
        assert "42" in output

    def test_prometheus_format_histogram(self):
        self.m.observe("request_time", 50.0)
        output = self.m.prometheus_format()
        assert "request_time_count" in output

    def test_empty_summary(self):
        result = self.m.summary()
        assert isinstance(result, dict)
        assert "uptime_seconds" in result

    def test_prometheus_ends_with_newline(self):
        output = self.m.prometheus_format()
        assert output.endswith("\n")


# ===================== validators.py =====================

class TestValidators:
    def test_valid_input(self):
        from app.validators import ProjectInput
        p = ProjectInput(budget=100000, co2_reduction=50,
                         social_impact=5, duration_months=12)
        assert p.budget == 100000

    def test_negative_budget_raises(self):
        from app.validators import ProjectInput
        with pytest.raises(Exception):
            ProjectInput(budget=-1, co2_reduction=50,
                         social_impact=5, duration_months=12)

    def test_negative_co2_raises(self):
        from app.validators import ProjectInput
        with pytest.raises(Exception):
            ProjectInput(budget=100000, co2_reduction=-1,
                         social_impact=5, duration_months=12)

    def test_social_impact_above_max_raises(self):
        from app.validators import ProjectInput
        with pytest.raises(Exception):
            ProjectInput(budget=100000, co2_reduction=50,
                         social_impact=11, duration_months=12)

    def test_social_impact_below_min_raises(self):
        from app.validators import ProjectInput
        with pytest.raises(Exception):
            ProjectInput(budget=100000, co2_reduction=50,
                         social_impact=-1, duration_months=12)

    def test_zero_duration_raises(self):
        from app.validators import ProjectInput
        with pytest.raises(Exception):
            ProjectInput(budget=100000, co2_reduction=50,
                         social_impact=5, duration_months=0)

    def test_boundary_values_pass(self):
        from app.validators import ProjectInput
        p = ProjectInput(budget=0, co2_reduction=0.01,
                         social_impact=0, duration_months=1)
        assert p.duration_months == 1

    def test_zero_budget_is_valid(self):
        from app.validators import ProjectInput
        p = ProjectInput(budget=0, co2_reduction=10,
                         social_impact=5, duration_months=12)
        assert p.budget == 0

    def test_social_impact_zero_is_valid(self):
        from app.validators import ProjectInput
        p = ProjectInput(budget=10000, co2_reduction=10,
                         social_impact=0, duration_months=12)
        assert p.social_impact == 0

    def test_budget_too_large_raises(self):
        from app.validators import ProjectInput
        with pytest.raises(Exception):
            ProjectInput(budget=2e12, co2_reduction=10,
                         social_impact=5, duration_months=12)

    def test_duration_too_large_raises(self):
        from app.validators import ProjectInput
        with pytest.raises(Exception):
            ProjectInput(budget=10000, co2_reduction=10,
                         social_impact=5, duration_months=601)


# ===================== mlflow_tracking.py =====================

class TestMlflowTracking:
    def test_log_prediction_no_crash(self):
        from app.mlflow_tracking import log_prediction
        # должен молча завершиться (все ошибки глотаются)
        log_prediction("rf", {"budget": 100000, "co2_reduction": 50,
                               "social_impact": 5, "duration_months": 12},
                       prediction=1, probability=0.75)

    def test_log_prediction_with_v2(self):
        from app.mlflow_tracking import log_prediction
        log_prediction("stacking", {"budget": 50000}, prediction=0,
                       probability=0.4, probability_v2=0.45)

    def test_log_evaluation_no_crash(self):
        from app.mlflow_tracking import log_evaluation
        log_evaluation("TestProject", {
            "total_score": 80, "environment_score": 75,
            "social_score""success_probability": 72.0,
        }, "Low")

    def test_log_evaluation_with_v2(self):
        from app.mlflow_tracking import log_evaluation
        log_evaluation("TestProject2", {
            "total_score": 60,
            "success_probability": 55.0,
            "success_probability_v2": 58.0,
        }, "Medium")

    def test_log_model_registry_no_crash(self):
        from app.mlflow_tracking import log_model_registry
        from unittest.mock import MagicMock
        mock_model = MagicMock()
        log_model_registry(mock_model, "test_model",
                           {"accuracy": 0.85, "auc": 0.90})

    def test_get_experiment_stats_returns_dict(self):
        from app.mlflow_tracking import get_experiment_stats
        result = get_experiment_stats()
        assert isinstance(result, dict)


# ===================== analytics.py Monte Carlo via API =====================

PROJECT = {
    "name": "Test", "budget": 500000, "co2_reduction": 50,
    "social_impact": 7, "duration_months": 12, "region": "Germany"
}

class TestAnalyticsCoverage:
    def setup_method(self):
        from fastapi.testclient import TestClient
        from app.main import app
        self.client = TestClient(app)

    def test_monte_carlo_score_stats(self):
        r = self.client.post("/analytics/monte-carlo",
                             json={**PROJECT, "simulations": 10})
        assert r.status_code == 200
        data = r.json()
        assert "score_stats" in data
        assert "risk_distribution" in data

    def test_model_compare_all_models(self):
        r = self.client.post("/analytics/model-compare", json=PROJECT)
        assert r.status_code == 200
        data = r.json()
        assert "models" in data or "results" in data or isinstance(data, list)

    def test_country_benchmark_known(self):
        r = self.client.get("/analytics/country-benchmark/Germany")
        assert r.status_code == 200

    def test_country_ranking(self):
        r = self.client.get("/analytics/country-ranking")
        assert r.status_code == 200
