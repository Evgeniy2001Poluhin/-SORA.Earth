# SORA.Earth AI Platform

[![CI](https://github.com/Evgeniy2001Poluhin/-SORA.Earth/actions/workflows/ci.yml/badge.svg)](https://github.com/Evgeniy2001Poluhin/-SORA.Earth/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Evgeniy2001Poluhin/-SORA.Earth)](https://github.com/Evgeniy2001Poluhin/-SORA.Earth/releases)
[![License](https://img.shields.io/badge/license-MIT-yellow)](LICENSE)

<!-- Badges here report; they do not assert.
     Three of the four that used to be here were hand-written images:
       tests-375/384 passing   the suite has run 2025 tests for months
       version-v0.2.1          the latest release is v0.3.0
       CI-passing-success      a static picture of the word "passing", shown
                               whether or not CI passes
     The last is the one that mattered: a badge that cannot fail is decoration
     claiming to be evidence. tests/test_readme_badges_report.py keeps them
     honest. -->


SORA.Earth AI Platform — полнофункциональная платформа для ESG‑оценки проектов, объяснимых ML‑предсказаний, страновой аналитики и автономного MLOps‑контурa с мониторингом и алертингом. [file:892]

Платформа собирает вместе FastAPI, PostgreSQL, Redis, APScheduler, Prometheus, Grafana и Docker Compose в единый production‑стек: отдельный scheduler‑процесс, персистентные логи, AI‑агент, админ‑панель и готовый к развёртыванию docker‑композ. [file:892]

---

## Возможности

- **ESG‑оценка проектов**
  - Оценка по трём компонентам: Environment / Social / Economic.
  - Итоговый ESG‑score, вероятность успеimpact. [file:892]
  - What‑if анализ и предсказания с учётом неопределённости.

- **Аналитика и страновые данные**
  - `/api/v1/analytics/country-benchmark/{country}` — ESG‑бенчмарк страны против глобального контекста. [file:892]
  - `/api/v1/analytics/country-ranking` — глобальный ESG‑рейтинг стран с пагинацией. [file:892]
  - Монте‑Карло симуляции, сравнение моделей, калькулятор GHG Scope 1/2/3. [file:892]

- **MLOps и пайплайны**
  - Drift detection (KS‑test) по ключевым фичам. [file:892]
  - Закрытый контур: drift → retrain → AUC‑валидация → promote / reject с decision log в PostgreSQL. [file:892]
  - Полный пайплайн: refresh внешних данных → drift → retrain → validate → promote. [file:892]

- **Operations / Admin*тформу и инициирует действия. [file:892]

- **Наблюдаемость и прод**
  - Prometheus‑метрики на `/metrics` — реестр `prometheus_client`, HTTP + доменные `sora_*`. Это тот путь, который скрейпит `infra/prometheus.yml`. `/api/v1/metrics/prometheus` отдаёт тот же реестр. [file:892][web:1033]
  - Grafana‑дашборд “SORA MLOps Overview” и 5 алертов (drift, retrain fail, AUC drop, latency, app down). [file:892]
  - Nginx reverse proxy (порт 80) с rate limiting, security‑заголовками, gzip и WebSocket‑проксированием. [file:892]

---

## Архитектура

```text
┌─────────────┐     ┌──────────────────────────────────────────────────┐
│   Nginx     │────▶│              FastAPI Application                 │
│   :80       │     │                                                  │
│ rate limit  │     │  /api/v1/evaluate /predict /predict/ex   │
                    └──────────┬───────────────┬───────────────────────┘
                               │               │
                     ┌─────────▼──────┐  ┌────▼─────┐
                     │ PostgreSQL     │  │ Redis    │
                     │ логи/состояние │  │ кэш/локи │
                     └─────────┬──────┘  └────┬─────┘
                               │              │
                        ┌──────▼──────┐  ┌────▼─────────┐
                        │ Scheduler   │  │ Prometheus   │
                        │ отдельный   │  │ + Grafana    │
                        │ процесс     │  │ дашборды     │
                   _ai_platform.git
cd sora_earth_ai_platform

# .env с минимальными настройками
cat > .env << EOF
POSTGRES_PASSWORD=sora2026
SORA_ADMIN_TOKEN=your-secret-token
GRAFANA_PASSWORD=sora2026
SECRET_KEY=your-jwt-secret

---

## Требования

**Python 3.11 или новее.** Объявлено в `pyproject.toml`; на этом же собирается
образ и работает CI. На более старом интерпретаторе пакет `app` не
импортируется вовсе — `app/rate_limit.py` и `app/secret_validation.py`
используют `X | None` в аннотациях, вычисляемых во время выполнения, — и почти
половина тестов перестаёт собираться с ошибкой из файла, не имеющего отношения
к запускаемому тесту.

`tests/test_python_version_is_declared.py` держит эту строку, `pyproject.toml`,
CI и `Dockerfile` в согласии друг с другом.

---

## Документация

Дополнительные материалы проекта:

- [Архитектура](docs/ARCHITECTURE.md)
- [DEMO-сценарий](docs/DEMO.md)
- [API обзор](docs/API.md)

## Architecture diagrams

C4-style диаграммы в `docs/diagrams/` (Mermaid, рендерятся прямо на GitHub).

- [Context](docs/diagrams/01-context.mmd) — пользователи и внешние системы
- [Container](docs/diagrams/02-container.mmd) — 7 Docker services + MLflow
- [Component](docs/diagrams/03-component.mmd) — внутренности FastAPI
- [Data flow](docs/diagrams/04-data-flow.mmd) — sequence для `/evaluate`
- [Deployment](docs/diagrams/05-deployment.mmd) — Docker Compose

## Screenshots

9 thesis-grade артефактов с подписями: [docs/screenshots/README.md](docs/screenshots/README.md).

Production: <https://sora-earth.online> — health at `/health`.
