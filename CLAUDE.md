# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SORA.Earth AI Platform is a full-stack ESG (Environmental, Social, Governance) evaluation and ML prediction system for sustainable projects. The platform provides explainable ML predictions, drift detection, A/B testing, MLflow tracking, and autonomous MLOps with scheduled retraining.

**Tech Stack:**
- Backend: FastAPI + PostgreSQL + Redis + SQLAlchemy + APScheduler
- Frontend: React 19 + TypeScript + Vite + TanStack Query + Zustand
- ML: scikit-learn (RandomForest, XGBoost), PyTorch MLP, SHAP for explainability
- Observability: Prometheus + Grafana + MLflow
- Infrastructure: Docker Compose with 7 services

**Deployment:** Docker Compose with separate `app` (FastAPI) and `scheduler` (APScheduler) containers, Nginx reverse proxy on port 80.

## Development Commands

### Backend Setup & Development

```bash
# Install Python dependencies
pip install -r requirements.txt

# Run migrations (Alembic)
alembic upgrade head

# Start FastAPI development server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Run specific test
pytest tests/test_auth.py -v

# Run all tests with coverage
pytest tests/ -v --cov=app --cov-report=html

# Run tests in specific category
pytest tests/test_drift* -v
pytest tests/test_calibration* -v
```

### Frontend Setup & Development

```bash
cd web
npm install
npm run dev      # Vite dev server on http://localhost:5173
npm run build    # TypeScript compilation + Vite build
npm run lint     # ESLint
npm run preview  # Preview production build
```

### Docker Compose

```bash
# Start all services (app, scheduler, postgres, redis, nginx, prometheus, grafana)
docker-compose up -d

# View logs
docker-compose logs -f app
docker-compose logs -f scheduler

# Rebuild and restart
docker-compose up --build -d

# Stop all services
docker-compose down

# Production deployment
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

### Database Migrations

```bash
# Create new migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1
```

## Architecture

### Backend Structure

```
app/
├── main.py                 # FastAPI app, middleware, route registration, model loading
├── database.py            # SQLAlchemy models (Evaluation, PredictionLog, DriftLog, etc.)
├── schemas.py             # Pydantic models for request/response validation
├── api/                   # API route modules (evaluate, predict, analytics, drift, etc.)
│   ├── evaluate.py        # /api/v1/evaluate - ESG scoring
│   ├── predict.py         # /api/v1/predict - ML predictions
│   ├── analytics.py       # Country benchmarks, rankings, Monte Carlo
│   ├── drift.py           # Drift detection endpoints
│   ├── retrain.py         # Model retraining
│   └── calibration.py     # Uncertainty quantification
├── ml/                    # ML models and training logic
├── drift/                 # Evidently-based drift detection
├── services/              # Business logic services
├── static/                # Static HTML files (admin dashboard, landing page)
└── scheduler.py           # APScheduler jobs (drift checks, retraining)
```

**Key files:**
- `app/main.py:313` - `make_features()` creates 9-feature DataFrame for RF model
- `app/main.py:387` - `calculate_esg()` computes ESG scores + region-aware recommendations
- `app/scheduler.py` - Scheduled jobs: drift detection every 6h, retrain on drift
- `app/drift_detection.py` - KS-test based drift detection with PostgreSQL decision log
- `run_scheduler.py` - Standalone scheduler process (runs in separate Docker container)

### Frontend Structure

```
web/src/
├── main.tsx               # App entry point with React Router
├── app/                   # Core application shell and layout
├── components/            # Reusable UI components (Button, Card, etc.)
├── features/              # Feature-specific modules (auth, evaluate, analytics, drift)
│   ├── auth/             # Login, registration, auth context
│   ├── evaluate/         # ESG evaluation form and results
│   ├── analytics/        # Country rankings, benchmarks, charts
│   ├── drift/            # Drift monitoring dashboard
│   └── predict/          # ML predictions with uncertainty
├── api/                   # API client with TanStack Query hooks
├── store/                 # Zustand state management
└── lib/                   # Utilities (axios config, formatters)
```

**Key patterns:**
- API calls use TanStack Query (`useQuery`, `useMutation`) with React hooks in `web/src/api/`
- State: Zustand for global state (`web/src/store/`), React Context for auth
- Routing: React Router v7 with loader functions for data fetching

### MLOps Pipeline

The closed-loop MLOps pipeline runs automatically via APScheduler in the `scheduler` service:

1. **Drift Detection** (every 6h): `app/scheduler.py:check_drift_job()`
   - Compares recent predictions (last 24h) vs training distribution
   - KS-test on key features (budget, co2_reduction, social_impact, duration_months)
   - Logs result to PostgreSQL `drift_log` table

2. **Auto-Retrain on Drift**: `app/scheduler.py:auto_retrain_on_drift_job()` (every 12h)
   - Checks latest drift log: if drift detected → trigger retrain
   - Retrains on historical `prediction_log` + labels from `evaluation` table
   - Validates new model: if AUC > 0.70 → promote; else reject
   - Decision logged to `retrain_log` table

3. **External Data Refresh** (daily): `app/scheduler.py:refresh_external_data_job()`
   - Fetches country ESG data from World Bank API
   - Updates `external_data` table in PostgreSQL

**Manual triggers:** `/api/v1/model/retrain`, `/api/v1/mlops/full-pipeline` (admin only)

### Models

Three models loaded at startup (`app/main.py:199-237`):
- **RandomForest** (`models/model.pkl`) - Primary ESG success predictor (9 features)
- **XGBoost** (`models/xgb_model.pkl`) - Alternative model (7 features)
- **PyTorch MLP** (`models/pytorch_mlp.pth`) - Neural network (SoraNet class)
- **Stacking Ensemble v2** (`models/ensemble_model_v2_cal.pkl`) - Calibrated stacking (11 features with category/region encoding)

Feature engineering: `make_features()` computes derived features (budget_per_month, co2_per_dollar, efficiency_score) + temporal features (year, quarter).

**SHAP explainability:** TreeExplainer initialized at startup (`app/main.py:261`). Endpoints: `/api/v1/predict/explain`, `/api/v1/explain/local`, `/api/v1/explain/global`.

### Database Schema

SQLAlchemy models in `app/database.py`:
- `Evaluation` - ESG evaluation history (project_name, esg_scores, success_prob)
- `PredictionLog` - Prediction logs with features + latency metrics
- `DriftLog` - Drift detection results (drift_detected, p_values, features)
- `RetrainLog` - Model retrain decisions (trigger, outcome, auc_old, auc_new)
- `User` - Auth users (hashed passwords with bcrypt)
- `RefreshJob` - External data refresh job status

**Connection:** PostgreSQL via SQLAlchemy async engine. Pool size: 10, max overflow: 20.

### API Organization

All routes prefixed with `/api/v1/`. Organized by domain in `app/api/`:
- **evaluate.py** - ESG scoring (`/evaluate`, `/evaluate/monte-carlo`)
- **predict.py** - ML predictions (`/predict`, `/predict/neural`, `/predict/stacking`)
- **analytics.py** - Analytics (`/analytics/country-benchmark/{country}`, `/analytics/country-ranking`)
- **drift.py** - Drift endpoints (`/drift/analyze`, `/drift/compare`)
- **retrain.py** - Model retraining (`/model/retrain`)
- **calibration.py** - Uncertainty (`/predict/uncertainty`, `/calibration/reliability`)
- **explain.py** - SHAP explainability (`/predict/explain`, `/what-if`)
- **ab_test.py** - A/B testing (`/ab/predict`, `/ab/stats`)

**Auth:** JWT tokens (access + refresh). Login at `/api/v1/auth/login`. Protected routes require Bearer token. Admin routes check `SORA_ADMIN_TOKEN` env var.

### Caching Strategy

Two-tier caching:
1. **Redis** (app/redis_cache.py) - Prediction results cached with TTL (5min). Key format: `pred:{hash(features)}`
2. **In-memory LRU** (app/cache.py) - Country benchmark data cached in-process

Cache invalidation: `/api/v1/cache/redis/invalidate` clears all prediction cache.

## Common Development Patterns

### Adding a New API Endpoint

1. Define Pydantic schema in `app/schemas.py`
2. Create route handler in `app/api/<domain>.py`
3. Register router in `app/main.py` (add to `_all_routers` or `api_v1.include_router()`)
4. Add tests in `tests/test_<domain>.py`

### Adding a New Feature Flag

Feature flags stored in `evaluation` table as JSONB column `metadata`. Check with:
```python
metadata = evaluation.metadata or {}
if metadata.get("feature_enabled"):
    # new behavior
```

### Uncertainty Quantification

Recent changes (commits `ad86863`, `309c64d`) added p5/p95 percentile ranges for predictions:
- Endpoint: `/api/v1/predict/uncertainty`
- Returns: `{prediction, p5, p95, range_pct, near_deterministic}`
- `near_deterministic` flag: True when (p95 - p5) < 10% (narrow confidence interval)

UI shows "≈det" badge for near-deterministic predictions (see `web/src/features/drift/DriftPage.tsx`).

## Testing

- **Test framework:** pytest with timeout=30s (`pytest.ini`)
- **Coverage target:** 375/384 tests passing (97.7%)
- **Test structure:** `tests/test_<domain>.py` mirrors `app/api/<domain>.py`
- **Fixtures:** `tests/conftest.py` provides FastAPI TestClient, mock database session

**Common test patterns:**
```python
# Test API endpoint
def test_evaluate_endpoint(client):
    response = client.post("/api/v1/evaluate", json={
        "budget": 100000,
        "co2_reduction": 150,
        "social_impact": 7,
        "duration_months": 24
    })
    assert response.status_code == 200
    assert "total_score" in response.json()
```

### Running Tests

- `pytest tests/test_auth.py::test_login -v` - Single test
- `pytest tests/test_drift* -k "baseline"` - Pattern matching
- `pytest --lf` - Rerun last failures
- `pytest -x` - Stop on first failure

## Environment Variables

Required in `.env`:
```bash
POSTGRES_PASSWORD=sora2026
SORA_ADMIN_TOKEN=<your-secret-token>
GRAFANA_PASSWORD=sora2026
SECRET_KEY=<your-jwt-secret>
DATABASE_URL=postgresql://sora:password@localhost:5432/sora_earth
REDIS_URL=redis://localhost:6379/0
RUN_SCHEDULER=false  # Set to true only in scheduler container
```

Optional:
- `SENTRY_DSN` - Sentry error tracking
- `MLFLOW_TRACKING_URI` - MLflow server (defaults to local sqlite)
- `SORA_ENV` - Environment name (development/production)

## Key Constraints & Gotchas

1. **Scheduler Architecture**: The scheduler runs in a separate Docker container (`scheduler` service). Do NOT set `RUN_SCHEDULER=true` in the `app` service or you'll have duplicate jobs. The scheduler shares the same codebase but runs `run_scheduler.py` instead of the FastAPI app.

   **Recreating the scheduler container runs five jobs immediately**, in
   addition to their schedule. This is not an APScheduler default — an interval
   trigger left alone first fires a full interval later (measured); the jobs are
   forced with `modify_job(next_run_time=now)` over
   `app/scheduler.py:RUN_IMMEDIATELY_ON_STARTUP`. So an ordinary deployment,
   **and a rollback**, writes to the database and calls external APIs.

   | job | side effect | why at startup | repeating it |
   |---|---|---|---|
   | `auto_run_ingesters` | rosstat + sber rows | **not stated** (a6d5ede) | safe, measured: same revision → `inserted=0`, zero row delta (#121) |
   | `auto_refresh_external_data` | World Bank pass + one `data_refresh_log` row | **not stated** (a6d5ede); the behaviour was known — the full history pass is gated behind `SORA_HISTORY_REFRESH` *because* this runs at startup | log row appended by design; heavy history pass off by default |
   | `refresh_forecast_metrics` | reads, sets Prometheus gauges | **not stated** (#11) | safe — gauges are set, never incremented |
   | `auto_openmeteo_ingestion` | one Open-Meteo fetch, `observed` rows | **not stated** (#11) | derived: identity is `{region}_{metric}_{event_time}`, so a repeat inside the same hour upserts |
   | `auto_openmeteo_air_quality_ingestion` | one Open-Meteo fetch, `observed` rows | **stated** (#82): otherwise the first rows arrive an hour after a deploy, and a restart to check the source shows nothing for an hour | same identity rule |

   One of the five has a written reason; four were inherited. Being in the tuple
   is not the same as having been chosen — whether the four should stay is #156.

   **What this means for acceptance.** The listed startup jobs may write during
   the deployment window. Attribute any change through `ingester_runs`, `source`
   and `source_revision`; only rows explained by those runs are expected. A
   deployment window is not evidence that a write came from somewhere else, and
   it is not a licence to accept an unexplained delta either.

2. **Feature Count Consistency**: The RF model expects exactly 9 features in this order: `["budget", "co2_reduction", "social_impact", "duration_months", "budget_per_month", "co2_per_dollar", "efficiency_score", "year", "quarter"]`. Always use `make_features()` to construct feature DataFrames.

3. **Model Versioning**: Models are loaded at app startup. To deploy a new model, replace files in `models/` directory and restart the `app` container. Old predictions remain cached in Redis until TTL expires or manual invalidation.

4. **Database Migrations**: Always create Alembic migrations for schema changes. The `migrations/` directory is mounted in Docker and runs on first `postgres` container startup.

5. **CORS Configuration**: CORS origins hardcoded in `app/main.py:144-151`. Add new origins there if deploying to new domains.

6. **Rate Limiting**: `SlowAPIMiddleware` in `app/rate_limit.py` counts every HTTP
   request per caller address. 100 req/min by default; `/api/v1/model/retrain` gets
   10 req/min in a bucket of its own **in addition to** the general one — a
   request to it is charged to both, so the tighter figure restricts rather than
   replaces. A refusal costs nothing: every budget is examined before any is
   written to, so being turned away at one does not spend another. Health,
   readiness, metrics and favicon paths are exempt — a probe on a schedule would
   otherwise spend a shared budget and make the health check flap.

   The counter lives in one process, so with several workers the effective budget
   multiplies by the worker count. It is a brake on a single noisy caller, not a
   defence against a distributed flood; that belongs at the edge.

   This paragraph previously described the limits as enforced while the middleware
   was a pass-through stub. Stating a control that does not exist is worse than
   stating none, because someone relies on it.

7. **Head Requests**: Custom middleware at `app/main.py:118-141` converts HEAD to GET internally. Do not set Content-Length manually in responses.

8. **Frontend Port**: Vite dev server runs on port 5173, proxies API requests to backend at port 8000 (configured in `web/vite.config.ts`).

## Monitoring & Observability

- **Prometheus metrics**: `/metrics` — the `prometheus_client` registry, custom
  `sora_*` metrics plus HTTP instrumentation. This is the path
  `infra/prometheus.yml` scrapes, and the only one configured.
  `/api/v1/metrics/prometheus` serves the same registry and is kept because
  several documents name it. `/metrics/prometheus` does not exist.

  Until #94 the `/api/v1` path assembled its own text from an in-process dict
  and carried **none** of the metrics declared in `app/prom_metrics.py`, while
  disagreeing with `/metrics` about four names it did carry. This section said
  otherwise, which is how the two were confused for months.

- **Operational counters**: `/api/v1/metrics` (JSON) — request counts by
  endpoint and status, uptime, response times. Never scraped by Prometheus.
- **Grafana dashboards**: http://localhost:3000 (admin/sora2026). Dashboard: "SORA MLOps Overview"
- **MLflow UI**: Tracking server at http://localhost:5000 (if running standalone MLflow)
- **Health checks**: `/health`, `/api/v1/health` (detailed), `/api/v1/ready` (readiness probe)

**Key metrics:**
- `sora_predictions_total` - Total predictions served
- `sora_drift_detected` - Drift events counter
- `sora_retrain_success/failure` - Retrain outcomes
- `sora_prediction_latency_seconds` - Prediction latency histogram

## External Dependencies

- **World Bank API**: Country ESG data fetched via `app/external_data.py`. Cached in PostgreSQL `external_data` table. Refresh job runs daily via scheduler.
- **ChromaDB**: Vector database for RAG (Retrieval-Augmented Generation) in `app/api/rag_api.py`. Optional feature, requires OPENAI_API_KEY.
- **Sentence Transformers**: Embedding model for RAG. Loaded on-demand when RAG endpoints called.

## Performance Notes

- **Prediction latency target**: <200ms (p95). Measured and logged to `prediction_log.latency_ms`.
- **Model inference**: RandomForest predict_proba takes ~5-10ms. Scaling adds ~1ms.
- **SHAP explanation**: TreeExplainer.shap_values() takes ~50-100ms. Avoid in hot paths.
- **Redis caching**: Reduces prediction latency by 10x for repeated inputs.
- **PostgreSQL connection pool**: Set to 10 connections. Monitor with `/api/v1/infra/data-refresh-status`.

## Useful Debugging Commands

```bash
# Check scheduler logs for drift/retrain activity
docker-compose logs -f scheduler | grep -E "(drift|retrain)"

# Query drift log directly
docker-compose exec postgres psql -U sora -d sora_earth -c "SELECT * FROM drift_log ORDER BY checked_at DESC LIMIT 5;"

# Check Redis cache stats
curl http://localhost:8000/api/v1/cache/redis

# View recent predictions
curl http://localhost:8000/api/v1/analytics/predictions-log?limit=10

# Manually trigger drift check
curl -X POST http://localhost:8000/api/v1/mlops/drift/observe

# Force model retrain (admin token required)
curl -X POST http://localhost:8000/api/v1/model/retrain \
  -H "Authorization: Bearer $SORA_ADMIN_TOKEN"
```

## Recent Changes

- **Uncertainty quantification** (commits `ad86863`, `309c64d`): Added p5/p95 percentile prediction ranges with `near_deterministic` flag for narrow intervals (<10% range).
- **Drift UI improvements** (commit `ac668bf`): Added "≈det" badge to UI for near-deterministic binary predictions.
- **Sequential runSweep** (commit `474cc41`): Fixed race condition in What-If analysis by switching from `Promise.all()` to sequential `for...of await`.
- **Discrepancy detection** (commit `efbf49a`): Enhanced calibration metrics with discrepancy analysis for probability-outcome alignment.

## Production Server

**Server Details:**
- Host: `45.137.60.67` (Ubuntu 24.04, hostname: melted-rose)
- SSH: `ssh root@45.137.60.67`
- Project directory: `/opt/sora_earth_ai_platform`
- Domain: https://sora-earth.online

**Deployment — one supported way, and this is it:**

```bash
cd /opt/sora_earth_ai_platform
./scripts/deploy_production.sh
```

Rollback is the same script: `./scripts/deploy_production.sh --rollback SHA`.

**A manual `docker compose up` / `build` / `restart` is not a deployment
procedure, and a green container health check is not evidence that the site
works.** The script exists because every incident in the month it was written
came from deploying by hand, and it is not a wrapper around convenience: it
recreates nginx *after* the backend, runs `nginx -t`, checks the upstream, the
certificate store, and finally `https://sora-earth.online/health` from outside.

This section previously carried the manual command above. On 2026-08-09 it was
followed, the backend was recreated, it took the address the scheduler had been
using, nginx kept the old one, and the public site returned 502 for four and a
half minutes -- while `docker inspect` reported both containers healthy with
zero restarts. The supported script would have prevented it (#129).

Recreating a single container by hand for a quick check is still recreating it:
if you do it, reload nginx afterwards and verify the public endpoint, or expect
the same failure.

**Production Containers (9):**
- `backend` - FastAPI application
- `scheduler` - APScheduler worker
- `nginx` - Reverse proxy + SSL termination
- `postgres` - PostgreSQL database
- `pgbouncer` - Connection pooler
- `redis` - Cache layer
- `mlflow` - MLflow tracking server
- `prometheus` - Metrics collection
- `grafana` - Monitoring dashboards

**Network Configuration:**
- External access: Only ports 80/443 exposed via nginx
- Internal services: Grafana/Prometheus bound to `127.0.0.1` only
- SSL/TLS: Managed by nginx (certificates in `/etc/letsencrypt/`)

### Working Protocol (CRITICAL)

When making changes on the production server, follow this protocol strictly:

**1. Before any patch - Validate syntax:**
```bash
# Show current diff
git --no-pager diff

# Validate Python syntax before applying changes
python3 -c "import ast; ast.parse(open('path/to/file.py').read())"
```

**2. Multi-line patches - Use Python heredoc + ast.parse:**
```bash
# CORRECT: Python heredoc (preserves indentation)
python3 << 'EOF'
with open('file.py', 'r') as f:
    content = f.read()
content = content.replace('OLD_TEXT', 'NEW_TEXT')
with open('file.py', 'w') as f:
    f.write(content)
import ast
ast.parse(content)  # Validate syntax
EOF

# WRONG: sed (breaks Python indentation)
sed -i 's/OLD/NEW/' file.py  # DO NOT USE
```

**3. Patch with anchors only:**
- Always search for unique anchor text before/after the change location
- If ANCHOR NOT FOUND → file is not modified (fail-safe)
- Verify anchors exist with `grep` before applying patch

**4. Use `git --no-pager`:**
```bash
# CORRECT: Prevents pager lockup over SSH
git --no-pager diff
git --no-pager log -5
git --no-pager show HEAD

# WRONG: Without --no-pager (can hang SSH session)
git diff  # DO NOT USE over SSH
```

### SSH Configuration

Add to `~/.ssh/config` to prevent timeout disconnects:
```
Host 45.137.60.67
    ServerAliveInterval 60
    ServerAliveCountMax 3
```

### Common Production Operations

```bash
# View logs
docker compose -f docker-compose.prod.yml logs -f backend
docker compose -f docker-compose.prod.yml logs -f scheduler

# Restart specific service
# A restart is not a deployment. For a code change use
# ./scripts/deploy_production.sh -- this only bounces the current image.
docker compose -f docker-compose.prod.yml restart backend

# Check container status
docker compose -f docker-compose.prod.yml ps

# Database backup
docker compose -f docker-compose.prod.yml exec postgres \
  pg_dump -U sora sora_earth > backup_$(date +%Y%m%d_%H%M%S).sql

# View recent drift checks
docker compose -f docker-compose.prod.yml exec postgres \
  psql -U sora -d sora_earth -c \
  "SELECT * FROM drift_log ORDER BY checked_at DESC LIMIT 5;"
```

### Monitoring Production

- **Application logs**: `docker compose -f docker-compose.prod.yml logs -f backend`
- **Grafana**: SSH tunnel required - `ssh -L 3000:127.0.0.1:3000 root@45.137.60.67`
- **Prometheus**: SSH tunnel required - `ssh -L 9090:127.0.0.1:9090 root@45.137.60.67`
- **Health check**: `curl https://sora-earth.online/health`
