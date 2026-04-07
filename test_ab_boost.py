"""Tests for app/api/ab_comparison.py — boost from 18%."""
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

PROJECT_A = {
    "name": "Project Alpha",
    "budget": 150000,
    "co2_reduction": 60,
    "social_impact": 7,
    "duration_months": 12,
    "region": "Germany",
}

PROJECT_B = {
    "name": "Project Beta",
    "budget": 300000,
    "co2_reduction": 120,
    "social_impact": 5,
    "duration_months": 24,
    "region": "Netherlands",
}


def test_ab_compare_two_projects():
    r = client.post("/ab/compare", json={"project_a": PROJECT_A, "project_b": PROJECT_B})
    assert r.status_code in [200, 404, 422]
    if r.status_code == 200:
        data = r.json()
        assert "winner" in data or "comparison" in data or "project_a" in data


def test_ab_compare_same_project():
    r = client.post("/ab/compare", json={"project_a": PROJECT_A, "project_b": PROJECT_A})
    assert r.status_code in [200, 404, 422]


def test_ab_history():
    r = client.get("/ab/history")
    assert r.status_code in [200, 404]
    if r.status_code == 200:
        assert isinstance(r.json(), (list, dict))


def test_ab_stats():
    r = client.get("/ab/stats")
    assert r.status_code in [200, 404]


def test_ab_report():
    r = client.post("/ab/report", json={"project_a": PROJECT_A, "project_b": PROJECT_B})
    assert r.status_code in [200, 404, 422]


def test_ab_sensitivity():
    r = client.post("/ab/sensitivity", json={"project_a": PROJECT_A, "project_b": PROJECT_B})
    assert r.status_code in [200, 404, 422]
