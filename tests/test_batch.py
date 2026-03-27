from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

SAMPLE_PROJECTS = [
    {"name": "Solar Farm Alpha", "budget": 100000, "co2_reduction": 60, "social_impact": 8, "duration_months": 24, "region": "Germany"},
    {"name": "Wind Park Beta", "budget": 200000, "co2_reduction": 80, "social_impact": 7, "duration_months": 36, "region": "Norway"},
    {"name": "Forest Gamma", "budget": 30000, "co2_reduction": 20, "social_impact": 9, "duration_months": 12, "region": "Brazil"},
]


def test_batch_evaluate():
    resp = client.post("/batch/evaluate", json={"projects": SAMPLE_PROJECTS})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert data["successful"] == 3
    assert data["failed"] == 0
    assert "batch_id" in data
    assert len(data["results"]) == 3


def test_batch_with_invalid():
    projects = SAMPLE_PROJECTS + [{"name": "Bad", "budget": -100, "co2_reduction": 0, "social_impact": 0, "duration_months": 0}]
    resp = client.post("/batch/evaluate", json={"projects": projects})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 4
    assert data["failed"] >= 1


def test_batch_get_by_id():
    resp = client.post("/batch/evaluate", json={"projects": SAMPLE_PROJECTS[:1]})
    batch_id = resp.json()["batch_id"]
    resp2 = client.get(f"/batch/{batch_id}")
    assert resp2.status_code == 200
    assert resp2.json()["batch_id"] == batch_id


def test_batch_not_found():
    resp = client.get("/batch/nonexistent")
    assert resp.status_code == 404


def test_list_batches():
    resp = client.get("/batch")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_ws_status():
    resp = client.get("/ws/status")
    assert resp.status_code == 200
    assert "active_connections" in resp.json()


def test_websocket_connect():
    with client.websocket_connect("/ws/live") as ws:
        ws.send_text("hello")
        data = ws.receive_json()
        assert data["echo"] == "hello"
        assert data["connections"] >= 1


def test_batch_processing_time():
    resp = client.post("/batch/evaluate", json={"projects": SAMPLE_PROJECTS})
    data = resp.json()
    assert data["processing_time_ms"] > 0
    assert data["processing_time_ms"] < 10000
