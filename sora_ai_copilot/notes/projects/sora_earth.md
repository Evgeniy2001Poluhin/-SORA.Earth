# SORA.Earth AI Platform

## Суть
ESG-оценка стран через ML-модели (RF, XGBoost, Neural Net, Ensemble + Stacking).
Backend: FastAPI + PostgreSQL + Redis + Docker Compose.

## Текущий статус (2026-04-09)
- v2.0.0 pilot-ready, 297 тестов, 92% coverage
- Модели: RF 95.78%, XGB 100%, NN 90.62%, ENS 99.12%
- Endpoints: /health, /evaluate, /predict/*, /analytics/*, /data/refresh, /scheduler/status
- Docker Compose: app, postgres, redis, grafana, prometheus
- APScheduler: auto-retrain 24h, auto-refresh external data 12h

## Известные проблемы
- APScheduler стартует в каждом gunicorn worker (дубли задач)
- data refresh долгий — World Bank API timeout/429/502, fallback на static benchmarks
- test_refresh_already_running timeout 30s
- OECD SDMX 429 Too Many Requests

## Архитектура
- app/main.py, app/external_data.py, app/scheduler.py
- app/api/ (evaluate, predict, analytics, data_pipeline)
- tests/ — pytest suite

## Стек
Python 3.9+, FastAPI, scikit-learn, XGBoost, PyTorch, SHAP, Optuna
PostgreSQL, Redis, Docker, Nginx, GitHub Actions, Prometheus, Grafana
