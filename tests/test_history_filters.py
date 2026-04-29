from datetime import datetime
from fastapi.testclient import TestClient
from app.main import app

c = TestClient(app)

def test_history_pagination_shape():
    r = c.get("/api/v1/history?limit=5&offset=0")
    assert r.status_code == 200
    j = r.json()
    assert set(j.keys()) >= {"items", "total", "limit", "offset"}
    assert j["limit"] == 5

def test_history_filter_risk_level():
    r = c.get("/api/v1/history?risk_level=LOW&limit=20")
    assert r.status_code == 200
    for it in r.json()["items"]:
        assert it["risk_level"] == "LOW"

def test_history_filter_date_range():
    today = datetime.utcnow().date().isoformat()
    r = c.get("/api/v1/history?date_from=" + today + "T00:00:00&limit=5")
    assert r.status_code == 200
    assert "items" in r.json()
