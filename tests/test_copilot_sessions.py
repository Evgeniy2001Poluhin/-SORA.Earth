"""Tests for /copilot/sessions endpoints + persistence integration."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    # mirror existing test pattern (see test_copilot_streaming.py)
    from app.main import app
    return TestClient(app)


def test_sessions_list_endpoint(client):
    r = client.get("/api/v1/copilot/sessions")
    assert r.status_code == 200
    body = r.json()
    assert "sessions" in body
    assert isinstance(body["sessions"], list)


def test_session_get_404_for_unknown(client):
    r = client.get("/api/v1/copilot/sessions/does_not_exist_xyz")
    assert r.status_code == 404


def test_session_delete_404_for_unknown(client):
    r = client.delete("/api/v1/copilot/sessions/does_not_exist_xyz")
    assert r.status_code == 404


def test_explain_creates_session(client):
    r = client.post("/api/v1/copilot/explain", json={
        "probability": 0.85,
        "features": {"co2_reduction": 1000, "budget": 2000000},
    })
    assert r.status_code == 200
    body = r.json()
    sid = body.get("session_id")
    assert sid and isinstance(sid, str)

    # Now session should be retrievable
    r2 = client.get(f"/api/v1/copilot/sessions/{sid}")
    assert r2.status_code == 200
    s = r2.json()
    assert s["id"] == sid
    assert len(s["messages"]) == 2  # user summary + assistant rec
    roles = [m["role"] for m in s["messages"]]
    assert roles == ["user", "assistant"]


def test_session_appears_in_list_after_explain(client):
    r = client.post("/api/v1/copilot/explain", json={
        "probability": 0.5,
        "features": {"co2_reduction": 500},
    })
    sid = r.json()["session_id"]

    r2 = client.get("/api/v1/copilot/sessions")
    ids = [s["id"] for s in r2.json()["sessions"]]
    assert sid in ids


def test_session_delete_works(client):
    # Create
    r = client.post("/api/v1/copilot/explain", json={
        "probability": 0.6,
        "features": {"budget": 100000},
    })
    sid = r.json()["session_id"]

    # Delete
    r2 = client.delete(f"/api/v1/copilot/sessions/{sid}")
    assert r2.status_code == 200
    assert r2.json()["deleted"] is True

    # Now 404
    r3 = client.get(f"/api/v1/copilot/sessions/{sid}")
    assert r3.status_code == 404
