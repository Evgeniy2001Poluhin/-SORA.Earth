# API обзор SORA.Earth AI Platform

> **Это выборка, а не полная спецификация.** Здесь описаны 33 из 162 пар
> (путь, метод) под `/api/`. Полная спецификация — `/docs` и `/redoc` живого
> приложения, они строятся из кода и полны по определению.
>
> Сказано прямо, потому что список без такой оговорки читается как полный: в
> #282 перечень из пяти моделей содержал две несуществующие и выглядел
> исчерпывающим. Каждый путь ниже сверяется с таблицей маршрутов приложения
> тестом `tests/test_api_doc_paths_resolve.py` — переименование маршрута в коде
> красит этот документ.
>
> Три записи были испорчены ещё в первом коммите (`997de75`, 7 мая):
> оборванные заголовки `### GET /api/v1/model/…` и `### POST /…` с затёкшим в
> путь хвостом описания. Восстанавливать их догадкой я не стал — выдуманная
> строка API неотличима от настоящей. Известно только семейство: одна из
> `/api/v1/model/*` (15 маршрутов, документирован один), вторая из
> `/api/v1/admin/ai/*`. Третью — `GET /api/v1/auth/me` — восстановить удалось:
> там уцелел весь путь, испорчены были только префикс и глагол (#294).

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

### POST `/api/v1/admin/ai/full-pipeline`
Запуск полного pipeline от имени AI.

## AI Teammate API

### GET `/api/v1/admin/ai-teammate/status`
Статус AI Teammate.

### POST `/api/v1/admin/ai-teammate/run`
Параметр `mode`: `observe` или `auto`.
Запуск AI Teammate.

## Auth API

### POST `/api/v1/auth/login`
Логин.

### POST `/api/v1/auth/login-json`
JSON login.

### POST `/api/v1/auth/refresh`
Обновление токена.

### GET `/api/v1/auth/me`
Текущий пользователь. Заголовок был испорчен там же и тогда же — читался как
`POST /api/`/api/v1/auth/me``, с лишним префиксом и неверным глаголом (#294).
Здесь, в отличие от двух удалённых записей, путь уцелел целиком, поэтому
восстановление не догадка: маршрут существует и отвечает только на GET.

## Monitoring API

### GET `/api/v1/metrics`
Операционные счётчики процесса в JSON: запросы по эндпоинтам и статусам,
uptime, времена ответа. Prometheus их не скрейпит.

### GET `/metrics`
Реестр `prometheus_client`: доменные `sora_*` и HTTP-инструментация. Это тот
путь, который настроен в `infra/prometheus.yml`.

### GET `/api/v1/metrics/prometheus`
Тот же реестр, что и `/metrics`. До #94 собирался вручную из словаря процесса и
не содержал ни одной из метрик, объявленных в `app/prom_metrics.py`.

### GET `/api/v1/system/metrics`
Системные метрики. Здесь стояло `/api/v1/system-metrics`, через дефис — такого
маршрута нет, и запрос по нему отдавал 404 (#294).

## Полная спецификация

Смотреть:
- `http://localhost:8000/docs`
- `http://localhost:8000/redoc`
