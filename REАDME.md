# 🌍 SORA.Earth AI Platform

> Production-grade MLOps platform for ESG sustainability assessment with autonomous AI agent, closed-loop retraining, and full observability stack.

[![Tests](https://img.shields.io/badge/tests-312%20passed-brightgreen)]()
[![Python](https://img.shields.io/badge/python-3.11-blue)]()
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688)]()
[![Docker](https://img.shields.io/badge/docker-compose-2496ED)]()
[![License](https://img.shields.io/badge/license-MIT-green)]()

---

## Architecture

```
┌─────────────┐     ┌──────────────────────────────────────────────────┐
│   Nginx     │────▶│              FastAPI Application                 │
│   :80       │     │                                                  │
│  rate limit │     │  /api/v1/evaluate    /api/v1/predict             │
│  security   │     │  /api/v1/predict/explain   /api/v1/batch        │
│  gzip       │     │  /api/v1/analytics/*  /admin/*                   │
└─────────────┘     │  /api/v1/admin/ai-teammate/*                     │
                    │                                                  │
                    │  ┌────────────┐  ┌───────────┐  ┌────────────┐  │
                    │  │ ML Models  │  │   SHAP    │  │  AI Agent  │  │
                    │  │ RF/XGB/NN  │  │ Explainer │  │ Teammate   │  │
                    │  │ Stacking   │  │           │  │ observe →  │  │
                    │  │ AUC: 0.98  │  │ waterfall │  │ decide →   │  │
                    │  │            │  │ beeswarm  │  │ execute    │  │
                    │  └────────────┘  └───────────┘  └────────────┘  │
                    └──────────┬───────────────┬───────────────────────┘
                               │               │
          ┌────────────────────┼───────────────┼────────────────────┐
          │                    │               │                    │
    ┌─────▼─────┐      ┌──────▼──────┐  ┌─────▼─────┐   ┌─────────▼──────┐
    │ PostgreSQL │      │    Redis    │  │ Scheduler │   │   Prometheus   │
    │  :5432     │      │   :6379     │  │ dedicated │   │    :9090       │
    │            │      │   cache     │  │ process   │   │                │
    │ retrain_log│      │   locks     │  │ cron jobs │   │  ┌──────────┐ │
    │ refresh_log│      │   TTL 24h   │  │ daily +   │   │  │ Grafana  │ │
    │ batch_res  │      │             │  │ weekly    │   │  │  :3000   │ │
    │ predictions│      │             │  │           │   │  │ 5 alerts │ │
    └────────────┘      └─────────────┘  └───────────┘   │  └──────────┘ │
                                                         └──────────────┘
```

**7 Docker services:** app · scheduler · nginx · postgres · redis · prometheus · grafana

---

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Python 3.9+ (for local development)

### Docker (recommended)

```bash
git clone https://github.com/your-repo/sora_earth_ai_platform.git
cd sora_earth_ai_platform

# Create .env
cat > .env << EOF
POSTGRES_PASSWORD=sora2026
SORA_ADMIN_TOKEN=your-secret-token
GRAFANA_PASSWORD=sora2026
SECRET_KEY=your-jwt-secret
EOF

# Start all 7 services
docker compose up -d --build

# Verify
curl http://localhost/health              # via Nginx
curl http://localhost:8000/health          # direct
open http://localhost:3000                 # Grafana
open http://localhost:8000/docs            # Swagger UI
```

### Local Development

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Start dependencies
docker compose up -d postgres redis

# Run app
uvicorn app.main:app --reload --port 8000

# Run tests
pytest tests/ -x -q
```

---

## API Reference

### Core ESG Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/evaluate` | ESG project evaluation (score, risk, recommendations) |
| POST | `/api/v1/predict` | ML prediction with confidence intervals |
| POST | `/api/v1/predict/compare` | Compare RF vs XGBoost vs Ensemble |
| POST | `/api/v1/predict/explain` | SHAP explainability (verdict, top_features, direction/impact) |
| POST | `/api/v1/batch/evaluate` | Batch evaluation (persisted to PostgreSQL) |
| GET | `/api/v1/countries` | Supported countries list |
| GET | `/api/v1/health` | Health check (DB, ML, cache, external data) |

### Analytics

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/analytics/model-compare` | Side-by-side model comparison |
| POST | `/api/v1/analytics/monte-carlo` | Monte Carlo risk simulation |
| GET | `/api/v1/analytics/country-benchmark/{country}` | Country vs global benchmark |
| GET | `/api/v1/analytics/country-ranking` | ESG country ranking |
| POST | `/api/v1/what-if` | What-if scenario analysis |
| POST | `/api/v1/ghg-calculate` | GHG Scope 1/2/3 calculator |
| GET | `/api/v1/report/pdf` | PDF report generation |

### MLOps Pipeline

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/mlops/auto-retrain` | Closed-loop: drift → retrain → AUC validate → promote/reject |
| POST | `/api/v1/mlops/full-pipeline` | Full: refresh → drift → retrain → validate → promote |
| GET | `/api/v1/mlops/drift` | Drift detection status (KS-test) |
| GET | `/api/v1/model/drift` | Current drift report |

### Admin & AI Teammate

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/admin/snapshot` | Platform health snapshot (AUC, counts, status) |
| GET | `/api/v1/admin/timeline` | Event timeline with retrain metrics |
| GET | `/api/v1/admin/diagnostics` | System diagnostics |
| GET | `/api/v1/admin/retrain-log` | Retrain history with decision log |
| POST | `/api/v1/admin/ai/refresh` | AI-triggered data refresh |
| POST | `/api/v1/admin/ai/retrain` | AI-triggered model retrain |
| POST | `/api/v1/admin/ai/report` | AI-generated platform report |
| POST | `/api/v1/admin/ai/full-pipeline` | AI-triggered full pipeline |
| POST | `/api/v1/admin/ai-teammate/run?mode=observe\|auto` | AI Teammate full cycle |
| GET | `/api/v1/admin/ai-teammate/status` | AI Teammate status check |

All admin endpoints require JWT authentication (`Authorization: Bearer <token>`).

---

## ML Models

| Model | Type | AUC | Features |
|-------|------|-----|----------|
| Random Forest | Ensemble | 0.98 | 9 |
| XGBoost | Gradient Boosting | 0.97 | 9 |
| Neural Network | MLP | 0.95 | 9 |
| Stacking Ensemble | Meta-learner | 0.98 | 9 |

**Features:** co2_emissions, renewable_energy_pct, gdp_per_capita, population_density, forest_area_pct, industrial_share_gdp, energy_intensity, political_stability, rule_of_law

**Explainability:** SHAP waterfall + beeswarm plots, per-feature direction/impact, verdict classification

---

## Closed-Loop MLOps

The platform implements a fully autonomous ML lifecycle:

```
┌──────────┐    ┌───────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  Data    │───▶│   Drift   │───▶│ Retrain  │───▶│ Validate │───▶│ Promote  │
│ Refresh  │    │ Detection │    │ Models   │    │ AUC ≥    │    │ or       │
│ WB+OECD  │    │  KS-test  │    │ RF+XGB+  │    │ old AUC  │    │ Reject   │
│          │    │           │    │ Ensemble │    │          │    │          │
└──────────┘    └───────────┘    └──────────┘    └──────────┘    └──────────┘
     ▲                                                                │
     │                    Decision Log → PostgreSQL                   │
     └────────────────────────────────────────────────────────────────┘
```

**Scheduler jobs:**
- `auto_refresh_external_data` — daily, World Bank + OECD
- `auto_closed_loop_daily` — daily, drift → retrain → validate
- `auto_full_pipeline_weekly` — Sunday 03:30 UTC, full end-to-end

**Trigger sources:** `scheduler` · `manual` · `ai_agent`

---

## AI Teammate

Autonomous agent that monitors platform health and takes corrective actions.

**Three-phase cycle:** observe → decide → execute

| Phase | What it does |
|-------|-------------|
| **Observe** | Checks 5 categories: data freshness (48h), retrain freshness (7d), model AUC (≥0.85), consecutive failures (≥3), drift |
| **Decide** | Generates decisions: no_action, recommend_refresh, recommend_retrain, execute_*, escalate |
| **Execute** | Auto mode only — calls refresh_live_data() or closed_loop_retrain() with trigger_source=ai_agent |

**Modes:**
- `observe` — read-only, generates recommendations
- `auto` — read + execute corrective actions

```bash
# Check status
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/admin/ai-teammate/status

# Run in auto mode
curl -X POST -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/admin/ai-teammate/run?mode=auto"
```

---

## Observability

### Prometheus Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `sora_prediction_latency_ms` | Histogram | Prediction endpoint latency |
| `sora_predictions_total{model}` | Counter | Predictions by model (rf/nn/stacking) |
| `sora_drift_detected_total` | Counter | Drift detection events |
| `sora_model_promoted_total` | Counter | Successful model promotions |
| `sora_model_rejected_total` | Counter | Rejected retrains (AUC degraded) |
| `sora_model_auc` | Gauge | Current model AUC |
| `sora_model_accuracy` | Gauge | Current model accuracy |
| `http_requests_total` | Counter | HTTP requests by method/status |
| `http_request_duration_seconds` | Histogram | HTTP latency distribution |

### Grafana Alerts

| Alert | Condition | Severity |
|-------|-----------|----------|
| Drift Detected | `increase(sora_drift_detected_total[5m]) > 0` | warning |
| Retrain Failed | `increase(sora_model_rejected_total[10m]) > 0` | critical |
| AUC Below 0.85 | `sora_model_auc < 0.85` for 5m | critical |
| High Latency | P95 > 500ms for 2m | warning |
| App Down | `up{job="sora-app"} == 0` for 1m | critical |

**Dashboards:** `http://localhost:3000` → SORA MLOps Overview

---

## Load Test Results

Locust load test: 50 concurrent users, 2 minutes, MacBook Air M-series.

| Metric | Value |
|--------|-------|
| Total requests | 4,528 |
| Throughput | 37.8 RPS |
| Error rate | 0.18% |
| Median latency | 4ms |
| P95 latency | 65ms |
| P99 latency | 330ms |

```bash
# Run load test
pip install locust
locust -f tests/locustfile.py --host http://localhost:8000 \
       --users 50 --spawn-rate 5 --run-time 2m --headless \
       --csv output/loadtest
```

---

## Project Structure

```
sora_earth_ai_platform/
├── app/
│   ├── main.py                  # FastAPI app, router registration
│   ├── scheduler.py             # APScheduler, closed-loop retrain, full pipeline
│   ├── training.py              # Model training logic
│   ├── database.py              # SQLAlchemy models, session management
│   ├── auth.py                  # JWT + API key authentication
│   ├── external_data.py         # World Bank + OECD data fetching
│   ├── drift_detection.py       # KS-test drift detection
│   ├── prom_metrics.py          # Prometheus domain metrics
│   ├── shap_explainer.py        # SHAP explainability
│   ├── batch.py                 # Batch evaluation schemas
│   ├── cache.py / redis_cache.py # Caching layer
│   ├── agents/
│   │   └── ai_teammate.py       # AI Teammate agent
│   ├── api/
│   │   ├── evaluate.py          # /evaluate endpoints
│   │   ├── predict.py           # /predict, /predict/explain
│   │   ├── infra.py             # /batch, /mlops, system endpoints
│   │   ├── analytics.py         # Monte Carlo, model compare, benchmarks
│   │   ├── admin_snapshot.py    # /admin/snapshot, diagnostics
│   │   ├── admin_timeline.py    # /admin/timeline
│   │   ├── admin_ai_control.py  # /admin/ai/* write layer
│   │   ├── ai_teammate_routes.py # /admin/ai-teammate/*
│   │   ├── retrain.py           # /mlops/auto-retrain
│   │   └── scheduler_routes.py  # /scheduler/status
│   └── static/
│       └── admin-dashboard.html # Admin SPA
├── tests/
│   ├── test_ai_teammate.py      # AI Teammate tests (9)
│   ├── test_closed_loop.py      # Closed-loop pipeline tests (9)
│   ├── test_api_public_v1.py    # Product API smoke tests (9)
│   ├── locustfile.py            # Load test
│   └── ...                      # 312 total tests
├── nginx/
│   └── nginx.conf               # Reverse proxy config
├── grafana/
│   └── provisioning/
│       ├── datasources/         # Prometheus datasource
│       ├── dashboards/          # SORA MLOps Overview
│       └── alerting/            # 5 alert rules
├── infra/
│   └── prometheus.yml           # Prometheus scrape config
├── docker-compose.yml           # 7 services
├── Dockerfile                   # Multi-stage Python 3.11
├── run_scheduler.py             # Dedicated scheduler entrypoint
├── requirements.txt
└── README.md
```

---

## Testing

```bash
# Full suite
pytest tests/ -x -q
# 312 passed, 4 skipped, 1 xfailed, 1 xpassed

# AI Teammate only
pytest tests/test_ai_teammate.py -v

# Closed-loop pipeline
pytest tests/test_closed_loop.py -v

# Product API smoke
pytest tests/test_api_public_v1.py -v
```

---

## Docker Services

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| app | python:3.11-slim | 8000 | FastAPI backend (RUN_SCHEDULER=false) |
| scheduler | python:3.11-slim | — | APScheduler dedicated process (RUN_SCHEDULER=true) |
| nginx | nginx:alpine | 80 | Reverse proxy, rate limiting, security headers |
| postgres | postgres:16-alpine | 5432 | Primary database |
| redis | redis:7-alpine | 6379 | Cache + distributed locks |
| prometheus | prom/prometheus | 9090 | Metrics collection |
| grafana | grafana/grafana | 3000 | Dashboards + alerting |

---

## Authentication

```bash
# Get JWT token
curl -X POST http://localhost:8000/api/v1/auth/login-json \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"sora2026"}'

# Use token
export TOKEN="<access_token from response>"
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/admin/snapshot
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| API Framework | FastAPI + Uvicorn |
| ML | scikit-learn, XGBoost, SHAP |
| Database | PostgreSQL 16 + SQLAlchemy + Alembic |
| Cache | Redis 7 |
| Scheduler | APScheduler (dedicated process) |
| Tracking | MLflow |
| Monitoring | Prometheus + Grafana |
| Reverse Proxy | Nginx |
| CI/CD | GitHub Actions (lint → pytest → docker build) |
| Load Testing | Locust |
| Containerization | Docker Compose (7 services) |

---

## License

MIT

---

*SORA.Earth AI Platform — Diploma Project, 2026*
