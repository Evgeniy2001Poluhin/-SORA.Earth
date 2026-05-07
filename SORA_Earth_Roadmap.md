SORA.Earth AI Platform — Roadmap v13
Дата обновления: 15 апреля 2026

Текущее состояние платформы
Платформа прошла полную стабилизацию backend-ядра, persisted state, explainability contract, тестового покрытия и admin dashboard: 312 тестов passed, 4 skipped, 1 xfailed, 1 xpassed. Backend собран вокруг FastAPI, APScheduler и PostgreSQL/Redis. Admin read layer и AI Control Layer write endpoints полностью стабилизированы. Admin Dashboard реализован как operational SPA с JWT-авторизацией, KPI-карточками, timeline и кнопками управления. Финализирован контракт /predict/explain (verdict, top_features, all_features, direction/impact). Реализован полный closed-loop MLOps цикл на двух уровнях: scheduler (closed_loop_retrain) и HTTP API (/api/v1/mlops/auto-retrain) — оба с drift detection → retrain → AUC validation → promote/reject → DB decision log.

Sprint 4 ~80% — null-метрики в snapshot починены, Alembic миграции синхронизированы, data_refresh_log расширена в prod-БД, фронтовые JS-ошибки устранены, API v1 product contract заморожен и покрыт smoke/integration тестами, стабилизирован explainability response contract под тесты.

Sprint 5 ~70% — полный closed-loop pipeline на обоих уровнях (scheduler + HTTP API): closed_loop_retrain() и /api/v1/mlops/auto-retrain оба делают drift → retrain → compare old/new AUC → promote/reject → DB log. _get_current_metrics() хелпер. Scheduler job auto_closed_loop_daily заменил auto_retrain_daily. 6 closed-loop тестов (3 API + 3 scheduler), require_admin подтверждён.

Компоненты
Компонент | Статус | Детали
FastAPI backend | ✅ Готово | /evaluate, /predict, /predict/compare, /report/pdf
ML модели | ✅ Готово | RF (AUC 0.98), XGBoost, Ensemble, Stacking, все на 9 фичах
SHAP explainability | ✅ Стабилизировано | /predict/explain, waterfall, beeswarm; контракт: verdict, top_features, all_features, direction/impact
Docker Compose | ✅ Готово | app + PostgreSQL + Redis + Prometheus + Grafana
External data | ✅ Стабилизировано | World Bank + OECD fallback + benchmarks, 6 индикаторов, 32 страны
TTL кэш | ✅ Готово | 24ч, invalidation
Drift Detection | ✅ Готово | KS-test drift по 4 фичам, /model/drift и /api/v1/mlops/drift
APScheduler | ✅ Обновлён | Единый scheduler.py, closed_loop_retrain + auto_refresh, Redis-lock
MLflow tracking | ✅ Готово | 100 экспериментов, model registry, /api/v1/mlflow/stats
Structured logging | ✅ Готово | JSON, Prometheus metrics
Health checks | ✅ Готово | DB + ML + cache + external_data
CI (GitHub Actions) | ✅ Готово | lint → pytest → docker build
Uncertainty quantification | ✅ Готово | /predict с confidence intervals
Temporal features | ✅ Готово | year, quarter в make_features
Auth / API keys | ✅ Готово | JWT + API key + admin roles + AuditMiddleware
Batch API | ✅ Готово | /batch/evaluate, in-memory history
WebSocket | ✅ Готово | /ws/live + connection manager
PDF reports | ✅ Готово | /report/pdf с FPDF
GHG calculator | ✅ Готово | /ghg-calculate (Scope 1/2/3)
What-if analysis | ✅ Готово | /what-if с вариациями параметров
Rate limiting | ✅ Готово | SlowAPI middleware + per-IP tracking
Sentry integration | ✅ Готово | optional DSN
Admin read layer | ✅ Стабилизирован | /admin/snapshot, /retrain-log, /timeline, /diagnostics
AI Control Layer (write) | ✅ Готово | /admin/ai/report, /admin/ai/retrain, /admin/ai/refresh
Monte Carlo simulation | ✅ Готово | /analytics/monte-carlo с risk distribution
Model compare | ✅ Готово | /analytics/model-compare (RF, XGBoost, Ensemble)
Country benchmarks | ✅ Готово | /analytics/country-benchmark, /analytics/country-ranking
DataRefreshLog extended | ✅ Готово | started_at, finished_at, duration_sec, trigger_source
Test suite | ✅ Стабилизирован | 312 passed, product API smoke suite + 6 closed loop tests
Admin Dashboard | ✅ Готово | JWT-авторизация, KPI, Timeline, Trigger Refresh/Retrain
Snapshot AUC + counts | ✅ Готово | rf/xgb/ensemble AUC; success/failed counts
Alembic migrations | ✅ Синхронизировано | head stamped, missing columns, checkfirst=True
Frontend JS (user-facing) | ✅ Стабилизирован | SyntaxError/showPage/URL-миграция починены, app.js+index.html чистые
API v1 product contract | ✅ Заморожен | smoke/integration tests: evaluate, history, countries, benchmarks, predict, what-if, export, report, health
Closed ML loop | 🟡 ~70% | scheduler + API endpoint оба с AUC validation + promote/reject, 6 тестов, DB decision log
_get_current_metrics | ✅ Готово | Читает последний успешный AUC из RetrainLog

Выявленные технические проблемы

Критичные

Проблема | Файл / область | Статус | Влияние
Конфигурация APScheduler в multi-worker проде | scheduler.py + деплой | ⏳ Частично решено | Требуется явно задать, какой процесс запускает scheduler

Средние

Проблема | Файл / область | Статус | Влияние
batch_history in-memory | main.py | ⚠️ Открыто | Batch results теряются при рестарте
Scheduler status без persisted state | scheduler.py | ⚠️ Открыто | Нет долгосрочного журнала запусков
/metrics парсинг на фронте | index.html fetchMetrics() | ⚠️ Косметика | "Failed to load metrics" — не ломает UI
Legacy endpoints без /api/v1 в app.js | app.js (predict/stacking, what-if, trends и др.) | ⚠️ Низкий | Работают через старые маршруты, миграция при фронт-рефакторинге
Тяжёлый ab-comparison путь в полном pytest | app/api/ab_comparison.py | ⚠️ Низкий | Не ломает suite, timeout noise
Full pipeline не собран в единую цепочку | scheduler.py / infra.py | ⚠️ Открыто | refresh → drift → retrain → validate → promote пока вызываются по отдельности

Закрытые в Sprint 4

Проблема | Статус | Итог
Admin Dashboard не работал (DOM-ошибки, битые теги) | ✅ Закрыто | Полностью переписан: JWT, KPI, timeline, actions
$('timeline').innerHTML → null | ✅ Закрыто | Корректные id-селекторы, null-safe
API-Key auth в dashboard вместо JWT | ✅ Закрыто | Переведён на /auth/login-json → Bearer token
AUC метрики null в /admin/snapshot | ✅ Закрыто | get_experiment_stats парсит rf/xgb/ensemble AUC
Success/failed counts null | ✅ Закрыто | success_count/failed_count в RetrainLogSummary
Alembic DuplicateTable crash | ✅ Закрыто | Alembic version stamped to head, checkfirst=True
data_refresh_log missing columns | ✅ Закрыто | started_at, finished_at, duration_sec, trigger_source
SyntaxError: Unexpected EOF (app.js:107) | ✅ Закрыто | "badge-high" — пропущена открывающая кавычка
SyntaxError: Unexpected identifier 'ta' (index.html:570) | ✅ Закрыто | li.textContent = data.recommendations;
SyntaxError: Unexpected token '!' (index.html:616) | ✅ Закрыто | if (!select || !select.value) — сломанное условие
ReferenceError: Can't find variable: showPage | ✅ Закрыто | Следствие SyntaxError — скрипт не парсился до определения showPage
404 /countries, /history (URL-миграция) | ✅ Закрыто | Все fetch в index.html и app.js переведены на /api/v1/*
rows.slice is not a function | ✅ Закрыто | Следствие 404 — HTML вместо JSON, исправлено URL-миграцией
Product API v1 не был явно заморожен | ✅ Закрыто | Добавлен tests/test_api_public_v1.py, 8 smoke/integration сценариев
Explain endpoint не соответствовал тестовому контракту | ✅ Закрыто | Добавлены поля verdict, top_features, all_features, base_value; стабилизированы direction/impact уровни

Закрытые в Sprint 5

Проблема | Статус | Итог
Нет orchestration endpoint для closed loop | ✅ Закрыто | Добавлен POST /api/v1/mlops/auto-retrain
Нет отдельного теста drift → retrain orchestration | ✅ Закрыто | Добавлен tests/test_closed_loop.py, 3 теста (skip, promote, reject)
Рассинхронизация моделей (9 vs 7 фичей) | ✅ Закрыто | Приведены к единому 9-feature contract retrain/predict/explain
Explainability и retrain конфликтовали после смены фичей | ✅ Закрыто | Контракт /predict/explain обновлён, тесты зелёные
/api/v1/mlops/auto-retrain без admin guard | ✅ Закрыто | require_admin уже присутствует в Depends, тесты через Bearer token
Closed loop без validation/promote step | ✅ Закрыто | closed_loop_retrain: compare old/new AUC, promote/reject, decision log в RetrainLog
Scheduler не использовал closed loop | ✅ Закрыто | auto_retrain_daily → auto_closed_loop_daily, вызывает closed_loop_retrain
Нет хелпера для получения текущих метрик | ✅ Закрыто | _get_current_metrics() в retrain.py, читает из RetrainLog
API endpoint auto-retrain без AUC validation | ✅ Закрыто | infra.py обновлён: old/new AUC comparison, promoted/rejected в response

Закрытые ранее (Sprint 1–3.6)

Проблема | Статус
DataRefreshLog extended fields | ✅ Закрыто
Module-level dependency_overrides | ✅ Закрыто
Рассинхронизация моделей (ранние версии) | ✅ Закрыто
Retrain-тесты перезаписывали pkl | ✅ Закрыто
Test pollution | ✅ Закрыто
Двойное определение fetch_indicator | ✅ Закрыто
Дублирование APScheduler | ✅ Закрыто
Отсутствие lock для refresh/retrain | ✅ Закрыто
Admin retrain log endpoint | ✅ Закрыто
Централизованный admin snapshot | ✅ Закрыто
OAuth2PasswordRequestForm обход | ✅ Закрыто
/model/status из in-memory | ✅ Закрыто
retrain_history in-memory | ✅ Закрыто
refresh_status in-memory | ✅ Закрыто
/admin/timeline, /admin/diagnostics | ✅ Закрыто

Фаза 1 — Stabilization & Data Automation

1.1 External data stabilization
✅ Очистить external_data.py, убрать дубли, согласовать fallback
✅ Indicator-level source tracking
⏳ Retry с exponential backoff
⏳ Timeout policy per source / per country

1.2 Scheduler hardening
✅ Единый scheduler.py, auto_retrain + auto_refresh, singleton guard
✅ Graceful startup/shutdown, /admin/timeline и /diagnostics
✅ Scheduler переведён на closed_loop_retrain
⏳ Прод-стратегия деплоя

1.3 Refresh hardening
✅ DataRefreshLog + CountryIndicatorHistory + Redis-lock
✅ started_at, finished_at, duration_sec, trigger_source
✅ Prod-БД синхронизирована с моделью
⏳ Persisted refresh state в dashboard KPI

1.4 Retrain hardening
✅ Redis-lock, RetrainLog в БД, /admin/retrain-log, trigger_source tracking
✅ _retrain_history полностью удалён
✅ _get_current_metrics хелпер для чтения текущего AUC

1.5 Test suite stabilization
✅ 312 passed, 0 failed, 4 skipped, 1 xfailed, 1 xpassed

1.6 Alembic / DB sync
✅ Alembic head stamped, DuplicateTable resolved
✅ checkfirst=True в init_db()

Фаза 2 — Frontend / Dashboard

2.1 Admin Dashboard (operational) ✅
✅ JWT-авторизация (username/password → Bearer token)
✅ KPI-карточки: Platform Health, Last Refresh, Last Retrain, Drift Status
✅ Data Refresh panel + Trigger Refresh кнопка
✅ Retrain Log panel + Trigger Retrain кнопка
✅ ML Models panel (experiment, runs, AUC, version)
✅ Scheduler panel (running, enabled, jobs, next run)
✅ Drift Detection panel (status, score, observations)
✅ Timeline (последние 48ч событий)
✅ Auto-refresh (15-секундный интервал)
✅ AUC метрики и success/failed counts в snapshot — починены

2.2 User-facing ESG UI (стабилизация) ✅ → 🟡
✅ JS SyntaxError починены (badge-high, textContent, if-condition)
✅ URL-миграция: evaluate, countries, history → /api/v1/*
✅ showPage навигация работает
⚠️ Legacy endpoints в app.js (predict/stacking, what-if, trends) — при фронт-рефакторинге
⚠️ /metrics парсинг — косметика
🔜 Полноценный user-facing рефакторинг (после backend freeze)

2.3 Real-time
🔜 SSE/WebSocket для notifications
🔜 Live drift-status, live refresh/retrain state
🔜 Визуальные алерты при сбоях

2.4 Visual analytics
🔜 Trend charts, SHAP в UI, side-by-side comparison, PDF export

Фаза 3 — Closed ML Loop + AI Teammate

3.1 Closed loop
✅ POST /api/v1/mlops/auto-retrain (drift → retrain → AUC validation → promote/reject, require_admin)
✅ closed_loop_retrain() в scheduler: drift → retrain → AUC validation → promote/reject
✅ API endpoint infra.py выравнен с scheduler: old/new AUC, promoted/rejected в response
✅ _get_current_metrics() хелпер
✅ Scheduler job auto_closed_loop_daily заменил auto_retrain_daily (03:00 UTC)
✅ Decision log: promoted/rejected записывается в RetrainLog с метриками
✅ 6 тестов: 3 API (test_closed_loop.py) + 3 scheduler (test_closed_loop_scheduler.py)
🔜 Full pipeline: refresh → drift → retrain → validate → promote (единая цепочка)
🔜 Dashboard: отображение promoted/rejected статусов в Timeline

3.2 Monitoring
🔜 Grafana dashboards, Prometheus metrics, Sentry
✅ /admin/timeline

3.3 AI Control Layer ✅
✅ GET snapshot, diagnostics, timeline
✅ POST ai/refresh, ai/retrain, ai/report
✅ Все с admin auth + trigger_source="ai_agent"

3.4 AI Teammate memory
🔜 Operational memory, read-only mode, escalation path

Фаза 4 — Production & Thesis

4.1 Production deployment
🔜 Dedicated scheduler, Nginx + HTTPS, secrets, backup

4.2 Load testing
🔜 Locust, 50+ RPS target

4.3 Thesis package
🔜 C4 диаграммы, A/B таблицы, AI teammate раздел

Приоритеты спринтов

Спринт | Фокус | Прогресс | Ключевой результат
Sprint 1 | Scheduler + orchestration | ✅ ~90% | Один scheduler, Redis locks
Sprint 2 | Persisted state | ✅ 100% | DataRefreshLog расширен, in-memory убраны
Sprint 3 | Admin diagnostics + тесты | ✅ 100% | snapshot, retrain-log, timeline, diagnostics
Sprint 3.5 | AI Control Layer | ✅ 100% | ai/report, ai/retrain, ai/refresh
Sprint 3.6 | Test suite stabilization | ✅ 100% | 286 passed, модели синхронизированы
Sprint 4 | Admin Dashboard + Frontend | 🟡 ~80% | Admin dashboard готов, JS-ошибки починены, API v1 frozen, explainability contract стабилизирован
Sprint 5 | Closed ML loop | 🟡 ~70% | closed_loop_retrain в scheduler + API endpoint оба с AUC validation, promote/reject, 6 тестов, decision DB log
Sprint 6 | AI teammate | 🔜 | AI observer поверх admin API
Sprint 7 | Deploy + load | 🔜 | Prod-ready, нагрузочные тесты
Sprint 8 | Thesis | 🔜 | Документация, диаграммы, защита

Рекомендуемый следующий шаг
Closed-loop pipeline функционален на обоих уровнях (scheduler и HTTP API) с AUC validation и decision logging. Следующий практический шаг — собрать full pipeline в единую цепочку: refresh → drift → retrain → validate → promote, затем отобразить promoted/rejected в Admin Dashboard Timeline.

Ближайшие задачи:

Full pipeline: refresh → drift → retrain → validate → promote (единый orchestration)

Dashboard: promoted/rejected статусы в Timeline

Коммит + push текущих изменений

Только потом — user-facing frontend pass

Архитектура AI Teammate

text
┌──────────────────────────────────────────────────┐
│              Claude / Local AI Agent             │
│      observe → analyze → suggest → confirm       │
└──────────────┬──────────────┬────────────────────┘
               │ READ         │ WRITE (audited)
               ▼              ▼
┌──────────────────────────────────────────────────┐
│               Admin Control API                  │
│  /admin/snapshot ✅  /admin/diagnostics ✅       │
│  /admin/timeline ✅  /admin/ai/refresh  ✅       │
│  /admin/ai/retrain ✅ /admin/ai/report ✅        │
└──────────────┬──────────────┬────────────────────┘
               │              │
     ┌─────────▼───────┐  ┌──▼──────────────────────┐
     │ Read Layer  ✅   │  │ Action Layer    ✅       │
     │ health           │  │ refresh_live_data       │
     │ scheduler_status │  │ retrain_models          │
     │ refresh_status   │  │ generate_report         │
     │ retrain_status   │  │ all with locks/audit    │
     │ drift_status     │  │ trigger_source tracking │
     └─────────────────┘  └──────────────────────────┘

     ┌──────────────────────────────────────────────┐
     │      Closed-Loop MLOps Pipeline  🟡→✅      │
     │  drift check → retrain → AUC validate        │
     │  → promote/reject → decision DB log           │
     │  scheduler: auto_closed_loop_daily 03:00 UTC  │
     │  API: POST /mlops/auto-retrain (admin)        │
     │  both with old/new AUC comparison             │
     └──────────────────────────────────────────────┘

          ┌─────────────────────────────────────┐
          │     Admin Dashboard ✅              │
          │  KPI · Timeline · Actions           │
          │  JWT auth · Auto-refresh            │
          │  AUC metrics · Counts ✅            │
          └─────────────────────────────────────┘