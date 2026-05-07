"""Coverage boost part 2: metrics.py + drift_detection.py"""
from app.metrics import Metrics
from app.drift_detection import DriftDetector


class TestMetrics:
    def test_inc(self):
        m = Metrics()
        m.inc("requests")
        m.inc("requests", 5)
        assert m.counters["requests"] == 6

    def test_observe(self):
        m = Metrics()
        m.observe("latency", 12.5)
        m.observe("latency", 7.3)
        assert len(m.histograms["latency"]) == 2

    def test_summary(self):
        m = Metrics()
        m.inc("hits", 3)
        m.observe("latency", 10.0)
        m.observe("latency", 20.0)
        s = m.summary()
        assert "uptime_seconds" in s
        assert s["counters"]["hits"] == 3
        assert s["latency_count"] == 2
        assert s["latency_avg_ms"] == 15.0
        assert s["latency_max_ms"] == 20.0

    def test_summary_empty(self):
        m = Metrics()
        s = m.summary()
        assert s["counters"] == {}

    def test_prometheus_format(self):
        m = Metrics()
        m.inc("req", 2)
        m.observe("latency", 5.0)
        out = m.prometheus_format()
        assert "uptime_seconds" in out
        assert "req 2" in out
        assert "latency_count 1" in out
        assert "latency_sum 5.0" in out

    def test_prometheus_empty(self):
        m = Metrics()
        out = m.prometheus_format()
        assert "uptime_seconds" in out


class TestDriftDetector:
    def test_insufficient_data(self):
        d = DriftDetector(window_size=50)
        d.set_baseline({"budget_mean": 100, "budget_std": 10})
        for i in range(5):
            d.add_observation({"budget": 100})
        r = d.check_drift()
        assert r["status"] == "insufficient_data"

    def test_stable(self):
        d = DriftDetector(window_size=50)
        d.set_baseline({"budget_mean": 100, "budget_std": 20,
                        "co2_reduction_mean": 50, "co2_reduction_std": 10})
        for i in range(20):
            d.add_observation({"budget": 100 + i % 5, "co2_reduction": 50})
        r = d.check_drift()
        assert r["status"] == "stable"
        assert "budget" in r["features"]
        assert r["features"]["budget"]["drift_level"] == "LOW"

    def test_drift_detected(self):
        d = DriftDetector(window_size=50)
        d.set_baseline({"budget_mean": 100, "budget_std": 5})
        for i in range(20):
            d.add_observation({"budget": 500})
        r = d.check_drift()
        assert r["status"] == "drift_detected"
        assert r["features"]["budget"]["drift_level"] == "HIGH"
        assert len(r["recent_alerts"]) > 0

    def test_medium_drift(self):
        d = DriftDetector(window_size=50)
        d.set_baseline({"budget_mean": 100, "budget_std": 10})
        for i in range(20):
            d.add_observation({"budget": 125})
        r = d.check_drift()
        assert r["features"]["budget"]["drift_level"] == "MEDIUM"

    def test_missing_feature(self):
        d = DriftDetector(window_size=50)
        d.set_baseline({"budget_mean": 100, "budget_std": 10})
        for i in range(20):
            d.add_observation({"other_field": 999})
        r = d.check_drift()
        assert r["status"] == "stable"

    def test_window_limit(self):
        d = DriftDetector(window_size=15)
        for i in range(30):
            d.add_observation({"budget": i})
        assert len(d.recent_data) == 15

    def test_all_four_features(self):
        d = DriftDetector(window_size=50)
        d.set_baseline({
            "budget_mean": 75000, "budget_std": 50000,
            "co2_reduction_mean": 45, "co2_reduction_std": 25,
            "social_impact_mean": 5.5, "social_impact_std": 2.5,
            "duration_months_mean": 18, "duration_months_std": 12,
        })
        for i in range(20):
            d.add_observation({"budget": 80000, "co2_reduction": 40,
                               "social_impact": 6, "duration_months": 20})
        r = d.check_drift()
        assert len(r["features"]) == 4
        for f in r["features"].values():
            assert "z_score" in f
            assert "baseline_mean" in f
            assert "current_mean" in f
