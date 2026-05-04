from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
PAYLOAD = {"name":"Solar","budget":300000,"co2_reduction":500,"social_impact":8,
           "duration_months":24,"category":"Solar","region":"ES"}
def test_pdf_en():
    r = client.post("/api/v1/reports/compliance.pdf", json=PAYLOAD)
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF-")
def test_pdf_ru():
    r = client.post("/api/v1/reports/compliance.pdf?lang=ru", json=PAYLOAD)
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF-")
def test_validation():
    r = client.post("/api/v1/reports/compliance.pdf", json={**PAYLOAD, "social_impact": 99})
    assert r.status_code == 422

def test_batch_pdf():
    payload = [
        {"name":"Solar A","budget":100000,"co2_reduction":300,"social_impact":7,
         "duration_months":12,"category":"Solar","region":"ES"},
        {"name":"Wind B","budget":500000,"co2_reduction":800,"social_impact":9,
         "duration_months":36,"category":"Wind","region":"DE"},
    ]
    r = client.post("/api/v1/reports/compliance-batch.pdf", json=payload)
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF-")
    assert len(r.content) > 3000
