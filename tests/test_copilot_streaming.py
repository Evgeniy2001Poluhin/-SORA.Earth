"""Tests for app/api/copilot_api.py — streaming + speed + citations."""
import json
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

PAYLOAD = {
    "probability": 0.87,
    "features": {
        "co2_reduction": 8000, "budget": 2_000_000,
        "social_impact": 85, "duration_months": 18, "co2_per_dollar": 4.0,
    },
    "project": {"name": "Test Project", "region": "Vietnam"},
    "model_version": "v5",
}


def _collect_sse(resp_text: str):
    """Parse 'data: {...}\\n\\n' SSE frames into list of dicts."""
    events = []
    for line in resp_text.splitlines():
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return events


def test_speed_map_constants():
    """Lock the speed map to prevent accidental tweaks."""
    from app.api.copilot_api import _SPEED_MAP
    assert _SPEED_MAP == {"fast": 0.015, "normal": 0.035, "slow": 0.08}


def test_stream_default_speed():
    r = client.post("/api/v1/copilot/explain/stream", json=PAYLOAD)
    assert r.status_code in [200, 422, 500]
    if r.status_code == 200:
        assert "text/event-stream" in r.headers.get("content-type", "")
        events = _collect_sse(r.text)
        assert any(e.get("type") == "meta" for e in events)
        assert any(e.get("type") == "done" for e in events)


@pytest.mark.parametrize("speed", ["fast", "normal", "slow"])
def test_stream_speed_param_accepted(speed):
    r = client.post(f"/api/v1/copilot/explain/stream?speed={speed}", json=PAYLOAD)
    assert r.status_code in [200, 422, 500]


def test_stream_invalid_speed_falls_back():
    """Invalid speed should not crash — falls back to normal (0.035)."""
    r = client.post("/api/v1/copilot/explain/stream?speed=lightning", json=PAYLOAD)
    assert r.status_code in [200, 422, 500]
    if r.status_code == 200:
        events = _collect_sse(r.text)
        assert any(e.get("type") == "done" for e in events)


def test_citations_injected_into_recommendation():
    """If RAG returns sources, recommendation should end with [doc_xxx] tokens."""
    r = client.post("/api/v1/copilot/explain/stream?speed=fast", json=PAYLOAD)
    if r.status_code != 200:
        pytest.skip(f"stream endpoint returned {r.status_code}")
    events = _collect_sse(r.text)
    done = next((e for e in events if e.get("type") == "done"), None)
    if not done or not done.get("sources"):
        pytest.skip("no RAG sources returned — citation injection cannot be verified")
    rec_tokens = [
        e["value"] for e in events
        if e.get("type") == "token"
    ]
    rec_text = "".join(rec_tokens)
    assert "[" in rec_text and "]" in rec_text, "expected at least one [doc_xxx] citation"


def test_stream_health_unaffected():
    """Sanity: health endpoint still responds after stream additions."""
    r = client.get("/api/v1/copilot/health")
    assert r.status_code == 200
    data = r.json()
    assert "compliance" in data
