from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_frameworks():
    r = client.get("/api/v1/compliance/frameworks")
    assert r.status_code == 200
    assert r.json()["frameworks"][0]["id"] == "CSRD_ESRS"

def test_csrd_solar():
    r = client.post("/api/v1/compliance/csrd", json={
        "name": "Solar France", "budget_usd": 250000,
        "co2_reduction_tons_per_year": 400, "social_impact_score": 8,
        "project_duration_months": 24, "category": "Solar Energy", "country": "France",
    })
    assert r.status_code == 200
    d = r.json()
    assert d["framework"] == "CSRD_ESRS"
    assert 0 <= d["overall_readiness"] <= 100
    assert "E1_Climate" in d["categories"]

def test_csrd_low():
    r = client.post("/api/v1/compliance/csrd", json={
        "name": "Tiny", "budget_usd": 1000,
        "co2_reduction_tons_per_year": 5, "social_impact_score": 2,
        "project_duration_months": 3,
    })
    assert r.status_code == 200
    d = r.json()
    assert d["status"] in ("partial", "gap")
    assert not d["audit_ready"]

def test_gap_endpoint():
    r = client.post("/api/v1/compliance/gap-analysis", json={
        "name": "T", "budget_usd": 100000,
        "co2_reduction_tons_per_year": 100, "social_impact_score": 5,
        "project_duration_months": 12,
    })
    assert r.status_code == 200
    assert "recommended_actions" in r.json()
