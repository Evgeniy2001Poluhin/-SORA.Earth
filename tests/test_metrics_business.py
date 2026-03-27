import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.middleware import METRICS


client = TestClient(app)


def test_evaluations_metrics_fields_exist_and_numeric():
    # Проверяем, что поля есть и это числа
    assert "evaluations_total" in METRICS
    assert "evaluations_avg_score" in METRICS

    assert isinstance(METRICS["evaluations_total"], int)
    assert isinstance(METRICS["evaluations_avg_score"], (int, float))


def test_system_metrics_json_contains_business_fields():
    r = client.get("/system/metrics")
    assert r.status_code == 200
    data = r.json()

    assert "evaluations_total" in data
    assert "evaluations_avg_score" in data
