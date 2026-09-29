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
    """Tests for /analytics/monte-carlo, which now returns 410 Gone (Finding 6 fix).

    The endpoint used an incorrect ESG formula and has been removed. These tests
    now verify that it returns 410 with the correct replacement information.
    """
    pytestmark = pytest.mark.timeout(60)

    def test_default_params(self):
        """The endpoint returns 410 Gone regardless of parameters."""
        r = client.post("/api/v1/analytics/monte-carlo", json=PROJECT)
        assert r.status_code == 410
        d = r.json()
        assert "detail" in d
        assert d["detail"]["replacement"] == "/api/v1/evaluate/monte-carlo"

    def test_custom_simulations(self):
        """Returns 410 even with custom simulations parameter."""
        data = {**PROJECT, "simulations": 100}
        r = client.post("/api/v1/analytics/monte-carlo", json=data)
        assert r.status_code == 410

    def test_max_simulations_cap(self):
        """Pydantic validation (Field(le=10000)) still rejects invalid values."""
        data = {**PROJECT, "simulations": 99999}
        r = client.post("/api/v1/analytics/monte-carlo", json=data)
        assert r.status_code == 422

    def test_max_simulations_valid(self):
        """Returns 410 even with valid max simulations."""
        data = {**PROJECT, "simulations": 10000}
        r = client.post("/api/v1/analytics/monte-carlo", json=data)
        assert r.status_code == 410

    def test_score_stats_keys(self):
        """Returns 410 (no score_stats in response anymore)."""
        r = client.post("/api/v1/analytics/monte-carlo", json=PROJECT)
        assert r.status_code == 410
        assert "detail" in r.json()

    def test_risk_distribution_sums(self):
        """Returns 410 (no risk_distribution in response anymore)."""
        r = client.post("/api/v1/analytics/monte-carlo", json=PROJECT)
        assert r.status_code == 410

    def test_different_regions(self):
        """Returns 410 for all regions."""
        for region in ["Germany", "Brazil", "Nigeria", "Japan"]:
            data = {**PROJECT, "region": region}
            r = client.post("/api/v1/analytics/monte-carlo", json={**data, "simulations": 50})
            assert r.status_code == 410


class TestModelCompare:
    def test_compare_returns_the_loaded_models(self):
        import app.main as main

        r = client.post("/api/v1/analytics/model-compare", json=PROJECT)
        assert r.status_code == 200
        d = r.json()
        assert "models" in d
        for name in ["RandomForest", "StackingEnsemble"]:
            assert name in d["models"]
        # XGBoost returns one probability for every project (finding 9) and is
        # no longer served in any comparison.
        assert "XGBoost" not in d["models"]
        # The neural network appears only when its weights are loaded (#320, #323):
        # it used to be a hand-written formula that was always present.
        assert ("NeuralNet" in d["models"]) == (main.nn_model is not None)

    def test_compare_has_best_model(self):
        r = client.post("/api/v1/analytics/model-compare", json=PROJECT)
        d = r.json()
        assert d["best_model"] in d["models"]

    def test_compare_probabilities_range(self):
        r = client.post("/api/v1/analytics/model-compare", json=PROJECT)
        for name, info in r.json()["models"].items():
            assert 0 <= info["probability"] <= 100
            assert info["prediction"] in (0, 1)


class TestCountryBenchmark:
    def test_all_benchmark_countries(self):
        for country in ["Germany", "France", "Japan", "Brazil"]:
            r = client.get(f"/api/v1/analytics/country-benchmark/{country}")
            assert r.status_code == 200
            assert r.json()["country"] == country

    def test_united_states_supported_or_global(self):
        r = client.get("/api/v1/analytics/country-benchmark/United States")
        assert r.status_code == 200
        assert r.json()["country"] in ["United States", "Global Average"]

    def test_unknown_returns_global(self):
        r = client.get("/api/v1/analytics/country-benchmark/Narnia")
        assert r.status_code == 200
        assert r.json()["country"] == "Global Average"

    def thas_keys(self):
        r = client.get("/api/v1/analytics/country-benchmark/Germany")
        bench = r.json()["benchmarks"]
        for key in ["co2_per_capita", "renewable_share", "esg_rank", "hdi"]:
            assert key in bench


class TestCountryRanking:
    def test_ranking_sorted(self):
        r = client.get("/api/v1/analytics/country-ranking")
        assert r.status_code == 200
        data = r.json()["data"]
        ranks = [d["esg_rank"] for d in data]
        assert ranks == sorted(ranks)

    def test_ranking_has_country_field(self):
        r = client.get("/api/v1/analytics/country-ranking")
        resp = r.json()
        assert "total" in resp
        assert "data" in resp
        for item in resp["data"]:
            assert "country" in item
            assert "esg_rank" in item

# --- Monte Carlo & Model Compare ---

def test_monte_carlo_basic():
    """POST /analytics/monte-carlo now returns 410 Gone (Finding 6 fix)."""
    r = client.post("/api/v1/analytics/monte-carlo", json={
        "name": "Test", "budget": 100000, "co2_reduction": 50,
        "social_impact": 7, "duration_months": 24,
        "region": "Germany", "simulations": 10
    })
    assert r.status_code == 410
    data = r.json()
    assert "detail" in data
    detail = data["detail"]
    assert detail["replacement"] == "/api/v1/evaluate/monte-carlo"

def test_monte_carlo_invalid():
    """Validation still happens before the 410 Gone."""
    r = client.post("/api/v1/analytics/monte-carlo", json={
        "budget": -1, "simulations": 5
    })
    assert r.status_code == 422

def test_model_compare_basic():
    r = client.post("/api/v1/analytics/model-compare", json={
        "budget": 100000, "co2_reduction": 50,
        "social_impact": 7, "duration_months": 24
    })
    assert r.status_code == 200
    data = r.json()
    assert "models" in data
    assert "RandomForest" in data["models"]
    assert "XGBoost" not in data["models"]  # finding 9

def test_model_compare_invalid():
    r = client.post("/api/v1/analytics/model-compare", json={"budget": -1})
    assert r.status_code == 422

# _run_monte_carlo was removed in Finding 6 fix - it used an incorrect ESG formula
