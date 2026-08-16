> **HISTORICAL — not the current plan.**
> Superseded by [`docs/DEVELOPMENT_ROADMAP.md`](docs/DEVELOPMENT_ROADMAP.md),
> which is the only active roadmap in this repository. Kept because it records
> why decisions were made; do not plan against it.

# ROADMAP v6.1 — SORA.Earth Defense Sprint

**Старт:** 27 мая 2026, 19:14 MSK
**Защита:** ~5–10 июня 2026
**Фокус:** код и улучшения проекта

## База
- Mac локально: main @ fabc82d, контейнеры healthy
- GitHub main: fabc82d
- Сервер прод: server-snapshot-2026-05-19 @ 9be54f0
- API endpoints: 136
- Frontend features: 12 (auth, calibration, compare, compliance, drift, evaluate, explain, history, home, map, mlops, region)
- DB tables: 9 (region_esg_scores, region_signals, evaluations, ...)
- MLflow Registry: esg-success-predictor v1, Stacking AUC 0.9917
- Active в API: model.pkl (RF AUC 0.9174, 737 samples)
- Ingesters: OpenAQ + Rosstat + Sber-VEB

## P0 — Критично (~7 ч)

| # | Задача | Время |
|---|---|---|
| 1.1 | MLflow Registry → /api/v1/predict (lazy load + cache) | 2 ч |
| 1.2 | Подключить scheduler_routes в maions из Postgres + retrain v3 | 1.5 ч |
| 1.5 | Cleanup мусора (.bak, WhatIf.tsx, mlflow_local.db, mlruns_local, htmlcov, venv, _legacy_*) | 30 мин |
| 1.6 | Stacking fix passthrough=False + reliability diagram | 1 ч |

## P1 — Killer-фичи (~10 ч)

| # | Задача | Время |
|---|---|---|
| 2.1 | LLM co-pilot endpoint /api/v1/ask | 3 ч |
| 2.2 | RAG context (SHAP + project + region) | 1.5 ч |
| 2.3 | Chat widget на фронте + streaming | 2 ч |
| 2.4 | Calibration Platt/Isotonic для XGB | 1 ч |
| 2.5 | A/B compare UI (champion vs challenger) | 1.5 ч |
| 2.6 | Drift Grafana panel | 1 ч |

## P2 — Polish (~6 ч)

| # | Задача | Время |
|---|---|---|
| 3.1 | Compliance UI (CSRD + gap-analysis) | 2 ч |
| 3.2 | Batch UI (массовые оценки) | 1.5 ч |
| 3.3 | Fairness audit по регионам РФ | 1 ч |
| 3.4 | README + API_CATALOG обновить | 30 мин |
| 3.5 | 10 скриншотов в thesis/figures | 1 ч |

## Календарь|
| 4 | Вс 31 | ОТДЫХ |
| 5 | Пн 1 | 2.1 + 2.2 |
| 6 | Вт 2 | 2.3 + deploy |
| 7 | Ср 3 | 2.5 + 2.6 + 3.1 |
| 8 | Чт 4 | 3.2 + 3.3 + 3.5 |
| 9 | Пт 5 | 3.4 + smoke + buffer |

## Что НЕ делаем (post-defense)
- Webhooks, Prophet, Embed widget, Status page, Onboarding tour
- UFW, WARP, Rate limiting

## Цели к 5 июня
- API endpoints: 136 → ~140
- Активная модель AUC: 0.9174 → 0.9917
- Killer features: 0 → 2 (LLM + A/B compare)
- Frontend pages: 12 → 13–14
