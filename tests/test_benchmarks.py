import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_country_benchmark_known():
    resp = client.get("/api/v1/analytics/country-benchmark/Germany")
    assert resp.status_code == 200
    data = resp.json()
    assert data["country"] == "Germany"
    assert data["benchmarks"]["co2_per_capita"] == 7.9
    assert data["benchmarks"]["esg_rank"] == 8


def test_country_benchmark_unknown():
    resp = client.get("/api/v1/analytics/country-benchmark/Atlantis")
    assert resp.status_code == 200
    data = resp.json()
    assert data["benchmarks"]["co2_per_capita"] == 4.7


def test_country_ranking():
    resp = client.get("/api/v1/analytics/country-ranking")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 20
    assert data[0]["country"] == "Sweden"
    assert isinstance(data[-1]["country"], str)  # last country in ranking
    ranks = [d["esg_rank"] for d in data]
    assert ranks == sorted(ranks)


def test_pdf_report():
    payload = {
        "budget": 100000,
        "co2_reduction": 50.0,
        "social_impact": 7.5,
        "duration_months": 12
    }
    resp = client.post("/api/v1/report/pdf", json=payload)
    assert resp.status_code == 200
    assert len(resp.content) > 100
