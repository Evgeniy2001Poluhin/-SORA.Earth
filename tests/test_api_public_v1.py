# tests/test_api_public_v1.py

from typing import Dict, Any

import pytest


PROJECT: Dict[str, Any] = {
    "name": "Solar Farm Alpha",
    "budget": 1000000,
    "co2_reduction": 50,
    "social_impact": 7,
    "duration_months": 24,
    "region": "Germany",
}


def test_evaluate_and_history_flow(client):
    # 1) Оценка проекта
    resp = client.post("/api/v1/evaluate", json=PROJECT)
    assert resp.status_code == 200
    data = resp.json()
    # базовые поля контракта
    assert "total_score" in data
    assert "risk_level" in data
    assert "success_probability" in data

    # 2) История оценок
    hist = client.get("/api/v1/history")
    assert hist.status_code == 200
    rows = hist.json()
    assert isinstance(rows, list)
    # проверяем, что есть хотя бы одна запись; детали можно ужесточить позже
    assert len(rows) >= 1


def test_countries_and_benchmarks(client):
    # 1) Список стран
    resp = client.get("/api/v1/countries")
    assert resp.status_code == 200
    countries = resp.json()
    assert isinstance(countries, dict)
    assert len(countries) > 0

    country_name = next(iter(countries.keys()))
    assert isinstance(country_name, str)
    assert country_name

    # 2) Бенчмарк по стране
    bench = client.get(f"/api/v1/analytics/country-benchmark/{country_name}")
    assert bench.status_code == 200
    bench_data = bench.json()
    assert isinstance(bench_data, dict)
    assert bench_data  # не пустой dict

    # 3) Рейтинг стран
    ranking = client.get("/api/v1/analytics/country-ranking")
    assert ranking.status_code == 200
    ranking_data = ranking.json()
    assert isinstance(ranking_data, dict)
    assert "data" in ranking_data
    assert isinstance(ranking_data["data"], list)
    assert ranking_data["data"]


def test_predict_and_uncertainty(client):
    # базовый predict
    base = client.post("/api/v1/predict", json=PROJECT)
    assert base.status_code == 200
    base_data = base.json()
    assert "prediction" in base_data or "score" in base_data

    # predict с интервалами
    unc = client.post("/api/v1/predict/uncertainty", json=PROJECT)
    assert unc.status_code == 200
    unc_data = unc.json()
    # допускаем любые названия полей, но проверяем структуру
    assert isinstance(unc_data, dict)
    assert unc_data


def test_what_if_scenarios(client):
    payload = {
        "project": PROJECT,
        "scenarios": [
            {"delta_budget": 0.1},
            {"delta_budget": -0.1},
        ],
    }
    resp = client.post("/api/v1/what-if", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    assert "base" in data
    assert any(k != "base" for k in data.keys())


def test_trends_and_prediction_history(client):
    trends = client.get("/api/v1/trends")
    assert trends.status_code == 200
    # допускаем разный формат (list/dict), главное — не 500

    hist = client.get("/api/v1/predictions/history")
    assert hist.status_code == 200
    h_data = hist.json()
    assert isinstance(h_data, list)


def test_export_csv_endpoints(client):
    # общий экспорт
    r1 = client.get("/api/v1/export/csv")
    assert r1.status_code in (200, 204)
    if r1.status_code == 200:
        assert "text/csv" in r1.headers.get("content-type", "")

    # экспорт prediction log
    r2 = client.get("/api/v1/predictions/export/csv")
    assert r2.status_code in (200, 204)
    if r2.status_code == 200:
        assert "text/csv" in r2.headers.get("content-type", "")


def test_report_pdf_generation(client):
    resp = client.post("/api/v1/report/pdf", json={"project": PROJECT})
    # если у тебя там синхронная генерация, скорее всего 200;
    # если асинхронная/квота — может быть 202/429, это можно ужесточить позже
    assert resp.status_code in (200, 202)
    if resp.status_code == 200:
        ct = resp.headers.get("content-type", "")
        assert "application/pdf" in ct or "application/octet-stream" in ct


def test_health_ready_ping(client):
    for path in ("/api/v1/health", "/api/v1/ready", "/api/v1/ping"):
        resp = client.get(path)
        assert resp.status_code == 200
        # форма ответа может быть любой (string/json), главное — живой сервис