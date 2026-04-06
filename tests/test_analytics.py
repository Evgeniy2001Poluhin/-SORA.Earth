"""Tests for analytics endpoints: Monte Carlo, benchmarks, model compare."""
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

PROJECT = {
    "name": "Test", "budget": 100000, "co2_reduction": 50,
    "social_impact": 7, "duration_months": 24, "region": "Germany"
}


import pytest

class TestMonteCarlo:
    pytestmark = pytest.mark.timeout(60)
    pytestmark = pytest.mark.timeout(60)
    def test_default_params(self):
        r = client.post("/analytics/monte-carlo", json=PROJECT)
        assert r.status_code == 200
        d = r.json()
        assert d["simulations"] == 1000
        assert "score_stats" in d
        assert "risk_distribution" in d

    def test_custom_simulations(self):
        data = {**PROJECT, "simulations": 100}
        r = client.post("/analytics/monte-carlo", json=data)
        assert r.status_code == 200
        assert r.json()["simulations"] == 100

    def test_max_simulations_cap(self):
        # Pydantic Field(le=10000) отклоняет значения > 10000
        data = {**PROJECT, "simulations": 99999}
        r = client.post("/analytics/monte-carlo", json=data)
        assert r.status_code == 422

    def test_max_simulations_valid(self):
        data = {**PROJECT, "simulations": 10000}
        r = client.post("/analytics/monte-carlo", json=data)
        assert r.status_code == 200
        assert r.json()["simulations"] == 10000

    def test_score_stats_keys(self):
        r = client.post("/analytics/monte-carlo", json=PROJECT)
        stats = r.json()["score_stats"]
        for key in ["mean", "std", "min", "max", "p5", "p25", "median", "p75", "p95"]:
            assert key in stats
            assert isinstance(stats[key], (int, float))

    def test_risk_distribution_sums(self):
        r = client.post("/analytics/monte-carlo", json=PROJECT)
        dist = r.json()["risk_distribution"]
        total = dist["low_risk_pct"] + dist["medium_risk_pct"] + dist["high_risk_pct"]
        assert 99 <= total <= 101

    def test_different_regions(self):
        for region in ["Germany", "Brazil", "Nigeria", "Japan"]:
            data = {**PROJECT, "region": region}
            r = client.post("/analytics/monte-carlo", json={**data, "simulations": 50})
            assert r.status_code == 200


class TestModelCompare:
    def test_compare_returns_all_models(self):
        r = client.post("/analytics/model-compare", json=PROJECT)
        assert r.status_code == 200
        d = r.json()
        assert "models" in d
        for m in ["RandomForest", "XGBoost", "NeuralNet", "StackingEnsemble"]:
            assert m in d["models"]

    def test_compare_has_best_model(self):
        r = client.post("/analytics/model-compare", json=PROJECT)
        d = r.json()
        assert d["best_model"] in d["models"]

    def test_compare_probabilities_range(self):
        r = client.post("/analytics/model-compare", json=PROJECT)
        for name, info in r.json()["models"].items():
            assert 0 <= info["probability"] <= 100
            assert info["prediction"] in (0, 1)


class TestCountryBenchmark:
    def test_all_benchmark_countries(self):
        for country in ["Germany", "France", "Japan", "Brazil"]:
            r = client.get(f"/analytics/country-benchmark/{country}")
            assert r.status_code == 200
            assert r.json()["country"] == country

    def test_united_states_supported_or_global(self):
        r = client.get("/analytics/country-benchmark/United States")
        assert r.status_code == 200
        assert r.json()["country"] in ["United States", "Global Average"]

    def test_unknown_returns_global(self):
        r = client.get("/analytics/country-benchmark/Narnia")
        assert r.status_code == 200
        assert r.json()["country"] == "Global Average"

    def thas_keys(self):
        r = client.get("/analytics/country-benchmark/Germany")
        bench = r.json()["benchmarks"]
        for key in ["co2_per_capita", "renewable_share", "esg_rank", "hdi"]:
            assert key in bench


class TestCountryRanking:
    def test_ranking_sorted(self):
        r = client.get("/analytics/country-ranking")
        assert r.status_code == 200
        data = r.json()["data"]
        ranks = [d["esg_rank"] for d in data]
        assert ranks == sorted(ranks)

    def test_ranking_has_country_field(self):
        r = client.get("/analytics/country-ranking")
        resp = r.json()
        assert "total" in resp
        assert "data" in resp
        for item in resp["data"]:
            assert "country" in item
            assert "esg_rank" in item
