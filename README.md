# SORA.Earth AI Platform

SORA.Earth AI Platform — это полнофункциональный backend-сервис для оценки ESG-проектов: от одиночных предиктов до batch-оценки, аналитики по странам, мониторинга и PDF-отчетов.

## Features

- **ESG-оценка проектов**
  - Оценка проекта по трём компонентам: Environment / Social / Economic.
  - Итоговый ESG-score, вероятность успеха, риск-профиль и текстовые рекомендации.
  - Поддержка регионального контекста (страна / регион).

- **Модели и ML-логика**
  - Ансамбль моделей (Random Forest, Gradient Boosting, нейросеть, stacking-ensemble).
  - SHAP-объяснения вклада признаков.
  - Монте‑Карло симуляции и сценарнme.
  - Prometheus-метрики (`/metrics/prometheus`) для подключения Grafana/Prometheus.

- **Аналитика и страновые данные**
  - `/analytics/country-ranking` — ESG/климатический рейтинг стран.
  - `/analytics/country-benchmark/{country}` — бенчмарк выбранной страны vs глобальное среднее.

- **Отчётность и мониторинг качества**
  - Генерация PDF-отчёта по проекту.
  - Логирование предиктов в MLflow.
  - Кэширование ответов для повторяющихся запросов.

## Quick start (локальный запуск)

### 1. Клонировать репозиторий и создать окружение

```bash
git clone <URL_ТВОЕГО_РЕПОЗИТОРИЯ>.git
cd sora_earth_ai_platform

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
2. Запуск приложМетрики в формате Prometheus:
http://localhost:8000/metrics/prometheus

Core API
Ниже — ключевые эндпоинты (полное описание см. в /docs / /redoc).

Оценка проекта

POST /evaluate
Тело запроса (пример):

json
{
  "name": "Solar Farm Alpha",
  "budget": 100000,
  "co2_reduction": 60,
  "social_impact": 8,
  "duration_months": 24,
  "region": "Germany"
}
Ответ включает:

total_score — итоговый ESG-score;

environment_score, social_score, economic_score;

success_probability — вероятность успеха (0–1);

risk_level — "Low" / "Medium" / "High";

recommendations — список текстовых советов.

POST /batch/evaluate
Принимает массив проектов в поле projects и возвращает список оценок.

Аналитика по странам

GET /analytics/country-ranking
Возвращает список стран с показат:

json
{
  "uptime_seconds": 1451.19,
  "counters": {
    "http_requests_total": 53,
    "http_200": 50,
    "http_404": 3
  },
  "request_duration_count": 53,
  "request_duration_avg_ms": 10.26,
  "request_duration_max_ms": 217.72
}
GET /metrics/prometheus
Те же данные в текстовом формате, совместимом с Prometheus / Grafana.

Frontend (минимальный UI)
Внутри приложения есть лёгкий HTML‑интерфейс (без фреймворков):

форма для оценки проекта;

блок с результатами (ESG-score, риск, рекомендации);

блок live‑метрик из /metrics;

поддержка авто‑оценки через параметры URL, например:

text
http://localhost:8000/?name=Test&budget=120000&duration_months=18&co2_reduction=70&social_impact=9&region=France
При переходе по такой ссылке форма заполняется параметрактурные элементы (OpenAPI-схема, метрики, health check).

Roadmap (frontend)
Этот репозиторий фокусируется на бэкенде. Поверх него можно развивать отдельный полноценный frontend (React/Vue) с разделами:

Evaluate / Dashboard

Stacking AI / SHAP

Compare Projects

World Map / Country analytics

GHG Calculator

Trends

System Metrics

