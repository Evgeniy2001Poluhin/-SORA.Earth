# Архитектура SORA.Earth AI Platform

## Назначение

SORA.Earth AI Platform — backend-first платформа для ESG-оценки проектов, explainable ML, country analytics, closed-loop MLOps и operational monitoring.

## Основные компоненты

### FastAPI Application
Центральный backend-сервис, который предоставляет:
- ESG evaluate endpoints
- prediction / explain endpoints
- analytics endpoints
- admin endpoints
- auth endpoints
- metrics / health / readiness endpoints

Ключевые маршруты:
- `/api/v1/evaluate`
- `/api/v1/predict`
- `/api/v1/predict/explain`
- `/api/v1/analytics/country-benchmark/{country}`
- `/api/v1/mlops/auto-retrain`
- `/api/v1/mlops/full-pipeline`
- `/api/v1/admin/snapshot`
- `/api/v1/admin/ai-teammate/status`

### ML Layer
ML-слой включает:
- Random Forest
- XGBoost
- Neural Network
- Stacking Ensemble
- SHAP explainability
- model comparison
- uncertainty-aware predi Redis
Используется для:
- кэширования повторяющихся ответов
- ускорения API
- distributed locks

### Scheduler
Отдельный процесс APScheduler выполняет:
- daily data refresh
- daily drift checks
- auto retraining
- weekly full pipeline

### Prometheus + Grafana
Observability-слой включает:
- экспорт `/api/v1/metrics/prometheus Nginx
Nginx используется как reverse proxy и даёт:
- единый вход через порт 80
- rate limiting
- security headers
- gzip
- проксирование к FastAPI

## Closed-loop MLOps

Pipeline реализует цикл:
1. refresh внешних данных  
2. drift detection  
3. retrain candidate model  
4. validation against previous model  
5. promote or reject  
6. запись решения в retrain log  

## AI Teammate

AI Teammate — автономный operational agent.

Фазы:
- observe
- decide
- execute

Он анализирует:
- freshness данных
- freshness retrain
- model quality thresholds
- consecutive failures
- drift state

## Docker Services

В docker-compose используются 7 сервисов:
- app
- scheduler
- nginx
- postgres
- redis
- prometheus
- grafana

##
Архитектура платформы сочетает backend API, explainable ML, automated retraining, admin tooling и full observability stack.
