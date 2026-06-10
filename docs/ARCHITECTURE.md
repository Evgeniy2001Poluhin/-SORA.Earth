# Архитектура SORA.Earth AI Platform

> Актуализировано: 2026-06-10. Все цифры сверены с живой системой
> (/api/v1/scheduler/status, /openapi.json, models/, docker-compose.yml).

## 1. Назначение
SORA.Earth AI Platform — backend-first платформа для ESG-оценки проектов и стран:
explainable ML, country & regional analytics, closed-loop MLOps и operational monitoring.

## 2. Технологический стек
- API: FastAPI + Uvicorn/Gunicorn (154 эндпоинта)
- Scheduler: APScheduler (BackgroundScheduler, UTC) в выделенном сервисе
- Хранилище: PostgreSQL 16 + Alembic
- Кэш / локи: Redis 7 (distributed locks для retrain)
- ML: scikit-learn (RandomForest, калибровка), XGBoost, Stacking, ensemble_v2
- Explainability: SHAP (beeswarm, waterfall)
- Experiment tracking: MLflow 2.x (regмировая карта (33 страны)
- GET /api/v1/map/countries/{code}
- GET /api/v1/map/russia — карта РФ (85 субъектов)
- GET /api/v1/map/russia/{region_code}
### Prediction & Explainability
- POST /api/v1/predict, /predict/v2, /api/v2/predict
- POST /api/v1/predict/explain, /predict/explain/waterfall (SHAP)
- POST /api/v1/predict/neural, /predict/stacking, /predict/uncertainty
- POST /api/v1/predict/compare, /api/v1/ab/predict
### Evaluate
- POST /api/v1/evaluate, /evaluate/monte-carlo, /evaluate/ranking, /batch/evaluate
### Analytics
- /analytics/country-benchmark/{country}, /analytics/country-ranking
- /analytics/model-compare, /analytics/monte-carlo
- /analytics/data-health, /analytics/metrics/model-health, /analytics/summary
### Scheduler / MLOps
- GET /api/v1/scheduler/status
- GET /api/v1/scheduler/retrain/history
- POST /api/v1/scheduler/retrain/trigger
- POST /api/v1/scheduler/refresh_external
### Drift
- GET /api/v2/drift/predictions

## 4. Карта России (85 субъектов РФ)
- Рендеринг: Leaflet (RussiaMap.tsx), полигоны из web/public/geo/russia.geo.json
  (FeatureCollection, EPSG:4326, properties {code, name}).
- Покрытие: все 85 субъектов, включая Республику Крым (RU-CR) и Севастополь (RU-SEV)
  — добавлены 2026-06-10 (commit 47f981b, 83 -> 85).
- Данные регионов: web/src/data/russia_regions.ts (столица, округ, население,
  координаты, ESG E/S/G + confidence).
- Режимы: «Население» и «ESG» (шкала 0–100). Раскраска по 8 федеральным округам.
- Население суммарно: 147.3M чел.

## 5. ML слой
### Production-модели (models/)
| Файл | Назначение |
|------|------------|
| model.pkl | Champion RandomForest |
| rf_model_cal.pkl | Калиброванный RF (CalibratedClassifierCV, isotonic, prefit) pkl | Метрики последнего retrain |
### Inference
- Продакшн: ensemble_model_v2.predict_proba с фоллбэком на RF.
- Метрики последнего retrain: accuracy 0.9563, F1 0.9637, ROC-AUC 0.9884, threshold 0.63.
- Explainability: SHAP beeswarm + waterfall. MLflow registry хранит версии.

## 6. Scheduler — 5 джобов (выделенный сервис)
Контейнер scheduler (run_scheduler.py), RUN_SCHEDULER=true, APScheduler, UTC.

| Job ID | Триггер | Назначение |
|--------|---------|------------|
| auto_closed_loop_daily | cron 03:00 UTC | drift -> retrain -> validate -> promote/reject |
| auto_refresh_external_data | interval 12h | обновление ESG (World Bank + OECD) |
| auto_full_pipeline_weekly | cron вс 03:30 UTC | refresh -> drift -> retrain -> validate |
| auto_run_ingesters | interval 24h | прогон всех ингестеров |
| health_ping | interval 5min | запись health-метрики |

tion (DriftDetector.check_drift())
3. retrain candidate (RF + изотоническая калибровка)
4. validation против действующей модели
5. promote / reject
6. запись в retrain log (try/finally — статус всегда терминальный)
Конкурентность защищена Redis-локом sora:lock:model_retrain.

## 8. AI Teammate
Автономный agent: observe -> decide -> execute. Анализирует freshness данных и retrain,
model quality thresholds, consecutive failures, drift state.

## 9. Docker-сервисы (7)
| Сервис | Команда / образ | RUN_SCHEDULER |
|--------|-----------------|---------------|
| app | uvicorn app.main:app (8000) | false |
| scheduler | python3 -u run_scheduler.py | true |
| redis | redis:7-alpine | — |
| postgres | postgres:16-alpine | — |
| prometheus | prom/prometheus | — |
| grafana | grafana/grafana | — |
| nginx | nginx:alpine (80) | — |

Ключевое реубъектов) + мировая карта,
explainable ML (RF + калибровка + ensemble_v2 + SHAP), автономный closed-loop MLOps
с выделенным планировщиком и полный observability-стек.
