# API обзор SORA.Earth AI Platform

## Base path

- `/api/v1`

## ESG / Core API

### POST `/api/v1/evaluate`
Основной endpoint ESG-оценки проекта.

### POST `/api/v1/batch/evaluate`
Batch-оценка массива проектов.

### GET `/api/v1/countries`
Список поддерживаемых стран.

### GET `/api/v1/health`
Проверка состояния системы.

## Prediction / Explainability

### POST `/api/v1/predict`
ML-предсказание по проекту.

### POST `/api/v1/predict/compare`
Сравнение нескольких моделей.

### POST `/api/v1/predict/explain`
Explainable prediction endpoint.

### POST `/api/v1/shap`
SHAP explain endpoint.

### POST `/api/v1/predict/uncertainty`
Предсказание с оценкой неопределённости.

## Analytics API

### GET `/api/v1/analytics/country-benchmark/{country}`
Страновой ESG-бенчмарк.

### GET `/api/v1/analytics/country-ranking`
Гланализ.

### POST `/api/v1/ghg-calculate`
Расчёт GHG Scope 1/2/3.

### POST `/api/v1/report/pdf`
Генерация PDF-отчёта.

## MLOps API

### GET `/api/v1/mlops/drift`
Проверка drift.

### POST `/api/v1/mlops/auto-retrain`
Автоматический retrain при drift.

### POST `/api/v1/mlops/full-pipeline`
Полный pipeline refresh → drift → retrain → validate → promote/reject.

### GET `/api/v1/model/ики модели.

### GET `/api/v1/model/status`
Статус модели.

## Admin API

### GET `/api/v1/admin/snapshot`
Сводка состояния платформы.

### GET `/api/v1/admin/timeline`
Таймлайн событий.

### GET `/api/v1/admin/diagnostics`
Диагностика системы.

### GET `/api/v1/admin/retrain-log`
История retrain.

### POST `/api/v1/admin/ai/refresh`
Запуск refresh от имени AI.

### POST `/ operational report.

### POST `/api/v1/admin/ai/full-pipeline`
Запуск полного pipeline от имени AI.

## AI Teammate API

### GET `/api/v1/admin/ai-teammate/status`
Статус AI Teammate.

### POST `/api/v1/admin/ai-teammate/run?mode=observe|auto`
Запуск AI Teammate.

## Auth API

### POST `/api/v1/auth/login`
Логин.

### POST `/api/v1/auth/login-json`
JSON login.

### POST `/api/v1/auth/refresh`
Обновление токена.

### POST `/api/`/api/v1/auth/me`
Текущий пользователь.

## Monitoring API

### GET `/api/v1/metrics`
Базовые метрики.

### GET `/api/v1/metrics/prometheus`
Метрики в формате Prometheus.

### GET `/api/v1/system-metrics`
Системные метрики.

## Полная спецификация

Смотреть:
- `http://localhost:8000/docs`
- `http://localhost:8000/redoc`
