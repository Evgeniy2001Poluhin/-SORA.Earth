import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

PROJECT = {
    "name": "Test Project",
    "budget": 100000,
    "co2_reduction": 50,
    "social_impact": 7,
    "duration_months": 12,
    "region": "Germany"
}

# ---- Health & Pages ----

def test_root():
    r = client.get("/")
    assert r.status_code == 200
    assert "SORA.Earth" in r.text

def test_health():
    r = client.get("/api/v1/health")
    assert r.status_code == 200

def test_countries():
    r = client.get("/api/v1/countries")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, dict)
    assert "Germany" in data

def test_history():
    r = client.get("/api/v1/history")
    assert r.status_code == 200
    assert isinstance(r.json(), list)

# ---- Evaluate ----

def test_evaluate_success():
    r = client.post("/api/v1/evaluate", json=PROJECT)
    assert r.status_code == 200
    data = r.json()
    assert "total_score" in data
    assert "risk_level" in data
    assert 0 <= data["total_score"] <= 100

# ---- Model Comparison ----

def test_predict_compare():
    r = client.post("/api/v1/predict/compare", json={"projects": [PROJECT]})
    assert r.status_code == 200
    data = r.json()
    assert "RandomForest" in data
    assert "XGBoost" in data

def test_predict_neural():
    r = client.post("/api/v1/predict/neural", json=PROJECT)
    assert r.status_code == 200

def test_predict_stacking():
    r = client.post("/api/v1/predict/stacking", json=PROJECT)
    assert r.status_code == 200

# ---- What-If ----

def test_what_if():
    r = client.post("/api/v1/what-if", json=PROJECT)
    assert r.status_code == 200

# ---- Monte Carlo ----

@pytest.mark.timeout(60)
def test_monte_carlo():
    r = client.post("/api/v1/analytics/monte-carlo", json=PROJECT)
    assert r.status_code == 200
    data = r.json()
    assert "mean_score" in data or "mean" in str(data).lower()

# ---- PDF Report ----

def test_pdf_report():
    r = client.post("/api/v1/report/pdf", json=PROJECT)
    assert r.status_code == 200
    assert "pdf" in r.headers.get("content-type", "")

# ---- SHAP ----

def test_shap():
    r = client.post("/api/v1/shap", json=PROJECT)
    assert r.status_code == 200

# ---- GHG Calculator ----

def test_ghg_calculate():
    r = client.post("/api/v1/ghg-calculate", json=PROJECT)
    assert r.status_code == 200

# ---- Metrics & Info ----

def test_model_info():
    r = client.get("/model-info")
    assert r.status_code == 200

def test_model_metrics():
    r = client.get("/model-metrics")
    assert r.status_code == 200

def test_system_metrics():
    r = client.get("/api/v1/system/metrics")
    assert r.status_code == 200

def test_regions():
    r = client.get("/api/v1/regions")
    assert r.status_code == 200

def test_trends():
    r = client.get("/api/v1/trends")
    assert r.status_code == 200

# ---- Analytics ----

def test_model_compare():
    r = client.post("/api/v1/analytics/model-compare", json=PROJECT)
    assert r.status_code == 200

def test_csv_export():
    r = client.get("/api/v1/export/csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers.get("content-type", "")
