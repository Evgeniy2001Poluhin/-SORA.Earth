# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SORA.Earth AI Platform is a full-stack ESG (Environmental, Social, Governance) evaluation and ML prediction system for sustainable projects. The platform provides explainable ML predictions, drift detection, A/B testing, MLflow tracking, and autonomous MLOps with scheduled retraining.

### The product, and an experiment beside it

Two things live in this repository and they are not the same thing. Read this
before measuring readiness against anything.

```
Product      SORA.Earth ESG platform -- this file and README.md
             In production, 162 published endpoints -- that is 162 (path,
             method) pairs under /api/, HEAD and OPTIONS excluded. The bare
             number was right and said nothing about what it counted; the app
             also has 180 unique paths and 157 of them under /api/ (#292).
             Remaining work is a punch-list, not a roadmap phase.

Experiment   Environmental crisis analytics -- ROADMAP_ENV_CRISIS_2026.md
             Phase 1 done; M2 closed as a negative result (no forecastable
             temporal target). Ingestion keeps accumulating labelled history
             and promises nothing.

Relation     The experiment neither replaces nor gates the product until it is
             explicitly promoted, which has not happened.
```

`ROADMAP_ENV_CRISIS_2026.md` opens with "SORA.Earth **должна стать** платформой
экологической аналитики" -- future tense. It is a proposal, never signed off as
a replacement for the definition above.

This paragraph exists because the distinction collapsed once already, on
2026-08-14: a readiness review measured the project against that roadmap's
phases and reported "end of Phase 1 of 8", which reads as *the product is one
eighth built*. It is not; the ESG platform is in production and serving. The
document being in the repository is not the same as it describing what the
repository is.

`docs/M2_EVALUATION_PROTOCOL.md` §10.3 states the one legitimate continuation
of the experiment -- forecasting weather and air quality over their 21 regions
-- and states equally that it may not be renamed an ESG forecast.

**Tech Stack:**
- Backend: FastAPI + PostgreSQL + Redis + SQLAlchemy + APScheduler
- Frontend: React 19 + TypeScript + Vite + TanStack Query + Zustand
- ML: scikit-learn (RandomForest, XGBoost), PyTorch MLP, SHAP for explainability
- Observability: Prometheus + Grafana + MLflow
- Infrastructure: Docker Compose -- 7 services in `docker-compose.yml` (development), 10 in `docker-compose.prod.yml` (nine long-running plus the one-shot `migrate`)

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

# Production deployment is not a compose command, and this section is about
# development. See "Production Server" below: ./scripts/deploy_production.sh
# is the only supported way, and the reason is written there.
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
├── database.py            # SQLAlchemy models (Evaluation, PredictionLog, RetrainLog, ...)
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
- `app/main.py` → `make_features()` builds the 9-column frame the RF model expects
- `app/main.py` → `calculate_esg()` computes ESG scores + region-aware recommendations
- `app/scheduler.py` - Thirteen scheduled jobs; see the table below. Drift is
  checked inside the daily closed loop, not by a job of its own.
- `app/drift_detection.py` - KS-test drift detection. It returns a verdict and
  writes nothing: the file holds no session, no `INSERT` and no table name.
  This line said "with PostgreSQL decision log"; there is no such log (#282).
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

This section named three functions that do not exist -- `check_drift_job`,
`auto_retrain_on_drift_job` and `refresh_external_data_job`, written there with
call parentheses as though they could be found -- and gave
periods no trigger in the code produces. On 2026-09-06 that cost a real
mistake: an operator was told the closed loop would next run "in about six
hours" because the document said every 12h. It runs daily at 03:00 UTC, and the
wait was for a run that could not happen.

The table below is generated from `scheduler.add_job(...)` and checked against
it by `tests/test_scheduler_jobs_match_the_doc.py`. **Edit the code, then
regenerate this; do not hand-edit the table.**

<!-- BEGIN SCHEDULED JOBS -->

| id | trigger | function |
|---|---|---|
| `refresh_forecast_metrics` | `IntervalTrigger(seconds=30)` | `refresh_forecast_metrics` |
| `health_ping` | `IntervalTrigger(minutes=5)` | *(inline lambda -- records a health row)* |
| `auto_source_health_check` | `IntervalTrigger(minutes=15)` | `scheduled_source_health_check` |
| `auto_openmeteo_ingestion` | `IntervalTrigger(hours=1)` | `scheduled_openmeteo_ingestion` |
| `auto_openmeteo_air_quality_ingestion` | `IntervalTrigger(hours=1)` | `scheduled_openmeteo_air_quality_ingestion` |
| `auto_data_quality_aggregation` | `IntervalTrigger(hours=1)` | `scheduled_data_quality_aggregation` |
| `auto_openaq_ingestion` | `IntervalTrigger(hours=1)` | `scheduled_openaq_ingestion` — **registered only when `_openaq_refusal is None`** |
| `auto_refresh_external_data` | `IntervalTrigger(hours=6)` | `scheduled_refresh_external_data` |
| `auto_pretrain_forecast` | `IntervalTrigger(hours=6)` | `scheduled_pretrain_forecast_models` |
| `auto_crisis_detection` | `IntervalTrigger(hours=6)` | `_scheduled_crisis_detection` |
| `auto_run_ingesters` | `IntervalTrigger(hours=24)` | `scheduled_run_ingesters` |
| `auto_closed_loop_daily` | `CronTrigger(hour=3, minute=0)` | `closed_loop_retrain` |
| `auto_full_pipeline_weekly` | `CronTrigger(day_of_week="sun", hour=3, minute=30)` | `full_pipeline_run` |

<!-- END SCHEDULED JOBS -->

Thirteen jobs, twelve of them unconditional. There is **no** separate drift-check
job: drift is checked inside `closed_loop_retrain`, once a day.

**What the closed loop does** (`app/scheduler.py:closed_loop_retrain`):

- Calls `app.api.drift.compute_drift()` -- the value-returning function, not the
  HTTP handler, which answers a `Response` on its unavailable branch.
- A check that could not run is **not** "no drift". `compute_drift` answers
  `status="unavailable"` and the loop declines to decide, returning
  `reason: "drift_check_unavailable"`. Reading that as "no drift" is what a
  `.get("drift_detected", False)` used to do, and it looks exactly like a
  healthy model.
- On drift, retrains on historical `prediction_log` plus labels from the
  `evaluation` table.
- Validates the new model on three independent refusals: the 95% **lower
  bound** of its AUC must clear 0.80 -- not the point estimate, since 0.85
  measured on 171 rows has a lower bound of 0.78 -- the run must have
  registered the model in MLflow, and it must not be more than 0.02 below what
  is already serving. Any one of the three rejects it. The closed loop applies
  all three; `POST /mlops/auto-retrain` currently applies only the last.
- Decision logged to the `retrain_log` table.

**Manual triggers:** `/api/v1/model/retrain`, `/api/v1/mlops/full-pipeline` (admin only)

### Models

Four models, loaded in `app/main.py` at import time. The heading said three
while listing four, and the line range pointed at a comment about database
defaults — the loads are spread across the file and move whenever it is edited
(#292):
- **RandomForest** (`models/model.pkl`) - Primary ESG success predictor (9 features)
- **XGBoost** (`models/xgb_model.pkl`) - Alternative model (7 features)
- **PyTorch MLP** (`models/pytorch_mlp.pth`) - Neural network (SoraNet class)
- **Stacking Ensemble v2** (`models/ensemble_model_v2_cal.pkl`) - Calibrated stacking (11 features with category/region encoding)

Feature engineering: `make_features()` computes derived features (budget_per_month, co2_per_dollar, efficiency_score) + temporal features (year, quarter).

**SHAP explainability:** TreeExplainer is initialized at startup in `app/main.py`. Endpoints: `/api/v1/predict/explain`, `/api/v1/explain/local`, `/api/v1/explain/global`.

### Database Schema

Sixteen SQLAlchemy models in `app/database.py`, with the table each one
creates. The table names are what `psql` needs, and they are not the class
names lowercased: most are plural, two are not.

| model | table |
|---|---|
| `Evaluation` | `evaluations` |
| `PredictionLog` | `predictions_log` |
| `DataRefreshLog` | `data_refresh_log` |
| `CountryIndicatorHistory` | `country_indicator_history` |
| `IngesterRun` | `ingester_runs` |
| `RetrainLog` | `retrain_log` |
| `BatchResultDB` | `batch_results` |
| `ForecastHistory` | `forecast_history` |
| `ForecastModelMetrics` | `forecast_model_metrics` |
| `RegionSignal` | `region_signals` |
| `RegionESGScore` | `region_esg_scores` |
| `WebhookSubscription` | `webhook_subscriptions` |
| `WebhookDelivery` | `webhook_deliveries` |
| `HealthPing` | `health_pings` |
| `EnvironmentalObservation` | `environmental_observations` |
| `EnvironmentalJobLog` | `environmental_job_log` |

The table is read from `__tablename__` and checked against the module by
`tests/test_docs_name_real_tables.py`, which also refuses any SQL in this file
that names a table no model creates. **Edit the code, then regenerate this; do
not hand-edit the table.**

**There is no `DriftLog` model and no `drift_log` table, and drift decisions
are not persisted anywhere.** This section named `DriftLog` and `RefreshJob`;
neither class exists -- the second one is `DataRefreshLog` -- and two debugging
commands in this file selected from `drift_log`, which answers `ERROR:
relation "drift_log" does not exist`. A list of five with two invented reads as
complete, and the commands were offered as the way to look at drift history,
which is when nobody is in a position to debug the documentation (#282).

The closest durable record is `retrain_log`: what the closed loop decided, not
what it measured. `compute_drift` computes a verdict and returns it; the
scheduler logs the decision that followed.

**Connection:** PostgreSQL through pgbouncer, via SQLAlchemy's **synchronous**
`create_engine` with `pool_pre_ping=True` and no other pool arguments -- so the
defaults apply, `pool_size=5` and `max_overflow=10`. This paragraph said "async
engine. Pool size: 10, max overflow: 20", and all three numbers were figures
nobody had set. `create_async_engine` appears nowhere in the codebase.

**There is no `User` table, and user accounts are not persisted.** This list
named one, "Auth users (hashed passwords with bcrypt)"; both halves were wrong,
and the second is the kind of error that gets repeated into a security review.

```
USERS_DB      in-memory, module-level dict in app/auth.py, rebuilt on every
              import from SORA_DEFAULT_{ADMIN,ANALYST,VIEWER}_PASSWORD.
              Production refuses to start if those are unset; the literal dev
              passwords apply only outside production.
hashing       Argon2id (argon2-cffi), not bcrypt. Two legacy SHA-256 verify
              paths remain in the code and are unreachable: all five writes of
              `hashed_password` go through _hash_password, and nothing loads a
              hash from anywhere else.
accounts      three roles, and only those three. POST /auth/register was
              removed in #169 -- it answered 200 and lost the account on the
              next restart.
```

`legacy_hash_count()` therefore cannot return anything but zero. That is a
property of the architecture, not evidence that a migration finished, and it is
why the removal plan in #25 has nothing to measure.

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
- **Suite size:** `pytest --collect-only tests/` reports 3082 cases. This line
  read "375/384 tests passing (97.7%)" for months -- a ratio nobody recomputed,
  wrong by a factor of eight. The passing figure is not restated here at all:
  CI computes it on every commit, and a copy in a document can only go stale
  (#292).
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

2. **Feature Count Consistency**: The RF model expects exactly 9 columns in this
   order: `["budget", "co2_reduction", "social_impact", "duration_months",
   "budget_per_month", "co2_per_dollar", "efficiency_score", "year", "quarter"]`.
   Always use `make_features()` to construct feature DataFrames.

   **Nine columns, seven of which carry information.** `year` and `quarter` are
   computed in `_do_retrain` as `datetime.utcnow().year` and the quarter of the
   same moment — the retrain's own clock, identical for every row in the frame.
   A constant column cannot separate anything, so the model fits on seven.
   Measured 2026-08-14; the shape of the defect is the same as
   `legacy_hash_count()`, which returns a number that cannot be other than
   zero: code that exists, looks like a feature, and does not do what its name
   says.

   They are left in place rather than removed: `models/model.pkl` was fitted
   with nine columns and would refuse eight, so dropping them is a retrain, not
   an edit. What must not happen is the count being read as nine working
   signals.

   Three columns in `data/projects.csv` are **not** features: `category`,
   `region`, `country_gdp_per_capita`. They are stored and unused by this
   model. Whether that is a decision or an oversight has not been recorded.

3. **Model Versioning**: Models are loaded at app startup. To deploy a new model, replace files in `models/` directory and restart the `app` container. Old predictions remain cached in Redis until TTL expires or manual invalidation.

4. **Database Migrations**: Always create Alembic migrations for schema changes. The `migrations/` directory is mounted in Docker and runs on first `postgres` container startup.

5. **CORS Configuration**: the origin list is hardcoded in `app/main.py`, in the `allow_origins` argument to `CORSMiddleware`. Add new origins there if deploying to new domains.

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

7. **Head Requests**: custom middleware in `app/main.py` converts HEAD to GET internally. Do not set Content-Length manually in responses.

8. **Frontend Port**: Vite dev server runs on port 5173, proxies API requests to backend at port 8000 (configured in `web/vite.config.ts`).

## Monitoring & Observability

- **Prometheus metrics**: `/metrics` — custom `sora_*` metrics plus HTTP
  instrumentation, served by `app/metrics_endpoint.py`. This is the path
  `infra/prometheus.yml` scrapes, and the only one configured.

  **Aggregated across gunicorn's four workers, and it was not until #262.**
  `prometheus_client` keeps each metric in the memory of the process that
  touched it. Measured on production 2026-09-06: five scrapes landed on two
  different workers, one reporting two `sora_telemetry_tasks_total` series and
  the other reporting none, for an event that had definitely happened. The
  error was never "about four times low" — gunicorn does not distribute
  round-robin, so a value could be anywhere between full and zero and moved
  between scrapes.

  `entrypoint.sh` sets `PROMETHEUS_MULTIPROC_DIR` and clears it once in the
  master before the fork; the scheduler leaves through the override branch
  above that line and never shares the directory. Three rules follow, and
  breaking any of them makes a metric report nothing while looking healthy:

  ```
  every Gauge         declares a multiprocess_mode -- without one you get a
                      series per process id, four where an alert expects one
  no Info metrics     they construct and set without error, then never appear
                      in the aggregated scrape (measured). Use a labelled
                      Gauge: sora_app_info is one, and renders identically
  no set_function     accepted, never collected (measured). Compute the value
                      in app/metrics_endpoint.py, which recomputes the retrain
                      staleness gauge immediately before reading the registry
  ```

  All twelve gauges use `mostrecent`: each is current state, not work done by a
  process, so summing four workers would be meaningless. `mark_process_dead`
  reaps only `live*` modes, so a worker recycle does not erase the last known
  value — which is the behaviour wanted here.
  `/api/v1/metrics/prometheus` serves the same registry and is kept because
  several documents name it. `/metrics/prometheus` does not exist.

  Until #94 the `/api/v1` path assembled its own text from an in-process dict
  and carried **none** of the metrics declared in `app/prom_metrics.py`, while
  disagreeing with `/metrics` about four names it did carry. This section said
  otherwise, which is how the two were confused for months.

- **The scheduler publishes its own** on `scheduler:9000/metrics`, scraped as a
  separate Prometheus job (#267). Eleven `sora_*` metrics are written only in
  that container — `sora_retrain_total`, `sora_full_pipeline_total`, the four
  forecast gauges and the five environmental ones — and until that target
  existed the process served no HTTP, so every one of them was set into memory
  nobody read and lost on the next restart. Its own job name rather than a
  second target under `sora-app`: both processes publish metrics of the same
  names.

  No multiprocess directory there. It is one process, and
  `app/scheduler_metrics.py` refuses to serve if `PROMETHEUS_MULTIPROC_DIR` is
  set rather than publishing whichever files it happens to find.

- **Operational counters**: `/api/v1/metrics` (JSON) — request counts by
  endpoint and status, uptime, response times. Never scraped by Prometheus.
- **Grafana dashboards**: http://localhost:3000 (admin/sora2026). Dashboard: "SORA MLOps Overview"
- **MLflow UI**: Tracking server at http://localhost:5000 (if running standalone MLflow)
- **Health checks**: `/health`, `/api/v1/health` (detailed), `/api/v1/ready` (readiness probe)

**Key metrics.** Names, labels and the process that writes each — checked
against the code by `tests/test_key_metrics_contract.py`, which fails if any
row here stops being true.

| metric | labels | written in | scrape job |
|---|---|---|---|
| `sora_predictions_total` | `model` | backend, `app/api/predict.py` | `sora-app` |
| `sora_prediction_latency_ms` | — (histogram) | backend, `app/api/predict.py` | `sora-app` |
| `sora_drift_detected_total` | — | scheduler, `app/scheduler.py` | `sora-scheduler` |
| `sora_retrain_total` | `status` | scheduler, `app/scheduler.py` | `sora-scheduler` |
| `sora_external_refresh_total` | `source`, `outcome` | scheduler, `app/scheduler.py` | `sora-scheduler` |

**Always name the job.** Both processes export the same metric *names* — every
`sora_*` symbol is defined once in `app/prom_metrics.py` and imported by both —
so `sora_drift_detected_total` alone returns two series, one of which is
permanently zero because that process never writes it. Query
`sora_drift_detected_total{job="sora-scheduler"}` or `sum by (job) (...)`, never
the bare name.

This list previously read:

```
sora_predictions_total          the only one that was right
sora_drift_detected             no such metric; the name is _total, and until
                                #266 nothing incremented it — while a Grafana
                                alert watched it
sora_retrain_success/failure    no such metric; it is sora_retrain_total{status},
                                and until #267 it was written in a process
                                Prometheus did not scrape
sora_prediction_latency_seconds no such metric; the name ends _ms
```

Three of the four could not have answered a question, and one of them had an
alert configured against it (#264, split into #266, #267 and this).

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

# What the closed loop decided. There is no drift_log table -- this command
# used to select from one, and answered "relation does not exist" (#282).
docker-compose exec postgres psql -U sora -d sora_earth -c "SELECT started_at, status, trigger_source FROM retrain_log ORDER BY started_at DESC LIMIT 5;"

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
- Host: `77.110.118.93` (Ubuntu 24.04, hostname: internal-amarant, Aeza NLs-3, Netherlands)
- SSH: `ssh root@77.110.118.93` — **key only**, password authentication is disabled
- Project directory: `/opt/sora_earth_ai_platform`
- Domain: https://sora-earth.online

The previous host, `45.137.60.67`, was **deleted for non-payment** around
2026-08-16 and the provider confirmed on 2026-09-02 that deleted services cannot
be restored. Everything on it is gone: the database, every environmental
observation collected since 2026-07-30, and the days that had started the M3 §7
clock. Nothing was held off the server. That address appears in the dated
reports under `.claude/`; those describe the world as it was and are left alone.

Two things about this host that are not guesses and will waste an hour if
rediscovered:

- **GitHub refuses anonymous `git-upload-pack` from this IP.** A public
  third-party repository fails identically, so it is the address and not this
  repository. The checkout pulls over SSH with a read-only deploy key at
  `/root/.ssh/github_deploy`. Separately, git protocol v2 fails here and v0
  works; `protocol.version = 0` is set in the host's global git config.
- **The `runtime_models` volume was chowned to `1000:1000` by hand** so the
  containers could take the activation lock. The image should create
  `/app/runtime` and make that unnecessary; until that ships, a recreated volume
  needs the same repair.

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

**Two configurations are bind-mounted single files, and a container pins the
inode it started with.** `nginx/nginx.conf` and `infra/prometheus.yml` both
change under a running container without reaching it -- a `git pull` replaces
the file and the process keeps reading the old one. The script recreates both
services for that reason, and then reads each configuration back out of its
container rather than trusting that the recreate worked. Prometheus was found
this way on 2026-09-07: the scrape target added by #267 had been on disk for
four days and was not in the running process, so the change was deployed and
inert (#275). Prometheus runs without `--web.enable-lifecycle`, so `POST
/-/reload` answers 403 and recreating is the only lever.

A prometheus failure warns and does not roll the deployment back: nginx serves
the site, prometheus watches it, and undoing a working deployment because the
watcher did not restart is the wrong trade. The last verification step asks
prometheus itself which jobs it is scraping (`promtool query instant ... up`),
because a target in the configuration and a target being collected are
different claims -- the first was true for the whole four days the second was
false.

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
Host sora
    HostName 77.110.118.93
    User root
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
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

# View recent closed-loop decisions. Not drift checks: the verdict is computed
# and returned, never stored, so retrain_log is the nearest durable record.
docker compose -f docker-compose.prod.yml exec postgres \
  psql -U sora -d sora_earth -c \
  "SELECT started_at, status, trigger_source FROM retrain_log ORDER BY started_at DESC LIMIT 5;"
```

### Monitoring Production

- **Application logs**: `docker compose -f docker-compose.prod.yml logs -f backend`
- **Grafana**: SSH tunnel required - `ssh -L 3000:127.0.0.1:3000 root@77.110.118.93`
- **Prometheus**: SSH tunnel required - `ssh -L 9090:127.0.0.1:9090 root@77.110.118.93`
- **Health check**: `curl https://sora-earth.online/health`
