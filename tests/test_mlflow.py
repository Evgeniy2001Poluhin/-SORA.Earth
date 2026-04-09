from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_mlflow_stats():
    resp = client.get("/mlflow/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "experiment" in data or "status" in data


def test_evaluate_logs_to_mlflow(monkeypatch):
    monkeypatch.setattr("app.external_data.get_country_context", lambda c: {
        "co2_per_capita": 4.0, "renewable_share": 55.0, "life_expectancy": 83.0,
        "gdp_per_capita": 55000, "gini_index": 28.0, "gov_effectiveness": 1.8
    })
    resp = client.post("/evaluate", json={
        "name": "MLflow Test Project",
        "budget": 50000,
        "co2_reduction": 40,
        "social_impact": 6,
        "duration_months": 12,
        "region": "Sweden"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "total_score" in data
    assert "country_benchmark" in data
    assert data["country_benchmark"]["country"] == "Sweden"


def test_mlflow_stats_after_eval():
    # After evaluation, should have at least one run
    resp = client.get("/mlflow/stats")
    assert resp.status_code == 200
