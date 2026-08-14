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
    """Оценка сохраняется и читается обратно.

    Маркер xfail здесь гласил «history empty after evaluate — either evaluate
    doesn't persist, or history reads from a different source» (#5). Ни то, ни
    другое: `/api/v1/history` отдаёт страницу `{items, total, limit, offset}`, а
    тест требовал `isinstance(rows, list)`. Измерено на этом же прогоне: после
    одной оценки `total == 1`, и запись лежит в `items`.

    Проверка теперь идёт по дельте, а не по «хотя бы одна»: набор не изолирован
    по базе между тестами, и непустая история могла бы остаться от соседа.
    """
    before = client.get("/api/v1/history")
    assert before.status_code == 200
    total_before = before.json()["total"]

    resp = client.post("/api/v1/evaluate", json=PROJECT)
    assert resp.status_code == 200
    data = resp.json()
    assert "total_score" in data
    assert "risk_level" in data
    assert "success_probability" in data

    hist = client.get("/api/v1/history")
    assert hist.status_code == 200
    page = hist.json()

    assert page["total"] == total_before + 1, (
        f"{total_before} -> {page['total']}: оценка не доехала до истории"
    )
    assert page["items"], "total вырос, а страница пуста"
    assert page["items"][0]["total_score"] == pytest.approx(data["total_score"]), (
        "первая запись страницы — не та оценка, которую только что сделали "
        "(порядок по created_at desc)"
    )


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


# The xfail here read "csv export endpoint returns 404 - implementation
# pending". Both routes exist: app/api/evaluate.py:287 and
# app/api/predict.py:233. The marker outlived the gap it described.
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