# CLAUDE.md

Guidance for Claude Code in this repository. It states current rules and facts.
The history behind them -- what an earlier version of this file said, and the
incident that corrected it -- is in the archived copy of this file
(`docs/archive/CLAUDE_md_before_compression_2026-11-05.md`, a historical
document), in `docs/incidents/` and in git. When a rule here seems arbitrary,
read the reason there before changing it.

## Project Overview

SORA.Earth AI Platform is a full-stack ESG evaluation and ML prediction system
for sustainable projects: explainable predictions, drift detection, A/B testing,
MLflow tracking, scheduled retraining.

### The product, and an experiment beside it

```
Product      SORA.Earth ESG platform -- this file and README.md.
             In production, 168 published endpoints: 168 (path, method) pairs under /api/,
             HEAD and OPTIONS excluded. Remaining work is a punch-list.

Experiment   Environmental crisis analytics -- ROADMAP_ENV_CRISIS_2026.md. Phase 1
             done; M2 closed as a negative result (no forecastable temporal
             target). Ingestion keeps accumulating labelled history.

Relation     The experiment neither replaces nor gates the product until it is
             explicitly promoted, which has not happened.
```


`ROADMAP_ENV_CRISIS_2026.md` is a proposal, never signed off as a replacement
for the product. Never measure the product's readiness against its phases.
`docs/M2_EVALUATION_PROTOCOL.md` §10.3 states the one legitimate continuation of
the experiment -- forecasting weather and air quality over their 21 regions --
and that it may not be renamed an ESG forecast.

**Tech stack:** FastAPI + PostgreSQL + Redis + SQLAlchemy + APScheduler; React 19
+ TypeScript + Vite + TanStack Query + Zustand; scikit-learn (RandomForest,
XGBoost), PyTorch MLP, SHAP; Prometheus + Grafana + MLflow. Docker Compose: 7
services in `docker-compose.yml` (development), 10 in `docker-compose.prod.yml`
(nine long-running plus the one-shot `migrate`). `app` (FastAPI) and `scheduler`
(APScheduler) are separate containers behind Nginx.

## Development Commands

```bash
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
pytest tests/test_auth.py -v

cd web && npm install
npm run dev      # Vite on http://localhost:5173, proxies the API to :8000
npm run build    # tsc + Vite build
npm run lint

docker-compose up -d          # development stack only
docker-compose logs -f app
docker-compose up --build -d
docker-compose down

alembic revision --autogenerate -m "description"
alembic downgrade -1
```

Production is never deployed with a compose command: see "Production Server".

## Architecture

```
app/
├── main.py            FastAPI app, middleware, routers, model loading;
│                      make_features() builds the RF frame, calculate_esg() scores
├── database.py        SQLAlchemy models
├── schemas.py         Pydantic request/response models
├── api/               routes by domain (evaluate, predict, analytics, drift, ...)
├── ml/  drift/  services/  static/
├── scheduler.py       APScheduler jobs (table below)
└── drift_detection.py KS-test drift; returns a verdict and writes nothing
run_scheduler.py       standalone scheduler process (its own container)

web/src/
├── app/ components/ features/ (auth, evaluate, analytics, drift, predict)
├── api/               TanStack Query hooks
├── store/             Zustand; React Context for auth
└── lib/
```

Routing is React Router v7 with loader functions.

### MLOps pipeline

The table is generated from `scheduler.add_job(...)` and checked by
`tests/test_scheduler_jobs_match_the_doc.py`. **Edit the code, then regenerate
this; do not hand-edit the table.**

<!-- BEGIN SCHEDULED JOBS -->

| id | trigger | function |
|---|---|---|
| `refresh_forecast_metrics` | `IntervalTrigger(seconds=30)` | `refresh_forecast_metrics` |
| `auto_observation_coverage` | `IntervalTrigger(minutes=15)` | `scheduled_observation_coverage` |
| `auto_source_health_check` | `IntervalTrigger(minutes=15)` | `scheduled_source_health_check` |
| `auto_openmeteo_ingestion` | `IntervalTrigger(hours=1)` | `scheduled_openmeteo_ingestion` |
| `auto_openmeteo_air_quality_ingestion` | `IntervalTrigger(hours=1)` | `scheduled_openmeteo_air_quality_ingestion` |
| `auto_data_quality_aggregation` | `IntervalTrigger(hours=1)` | `scheduled_data_quality_aggregation` |
| `auto_openaq_ingestion` | `IntervalTrigger(hours=1)` | `scheduled_openaq_ingestion` — **registered only when `_openaq_refusal is None`** |
| `auto_refresh_external_data` | `IntervalTrigger(hours=6)` | `scheduled_refresh_external_data` |
| `auto_pretrain_forecast` | `IntervalTrigger(hours=6)` | `scheduled_pretrain_forecast_models` |
| `auto_crisis_detection` | `IntervalTrigger(hours=6)` | `_scheduled_crisis_detection` — **registered only when `_crisis_refusal is None`** |
| `auto_run_ingesters` | `IntervalTrigger(hours=24)` | `scheduled_run_ingesters` |
| `auto_closed_loop_daily` | `CronTrigger(hour=3, minute=0)` | `closed_loop_retrain` |
| `auto_full_pipeline_weekly` | `CronTrigger(day_of_week="sun", hour=3, minute=30)` | `full_pipeline_run` |

<!-- END SCHEDULED JOBS -->

Thirteen jobs, twelve of them unconditional. There is **no** separate drift-check
job: drift is checked inside `closed_loop_retrain`, once a day at 03:00 UTC.

**The closed loop** (`app/scheduler.py:closed_loop_retrain`):

- Calls `app.api.drift.compute_drift()`, the value-returning function, not the
  HTTP handler.
- A check that could not run is **not** "no drift": `compute_drift` answers
  `status="unavailable"` and the loop returns `reason: "drift_check_unavailable"`.
  Never read it with `.get("drift_detected", False)`.
- On drift, retrains on historical `prediction_log` plus labels from the
  `evaluation` table.
- Validates on three refusals: the 95% **lower bound** of AUC must clear 0.80
  (not the point estimate), the run must be registered in MLflow, and it must not
  be more than 0.02 below the serving model. **Both gated paths apply all three**:
  the closed loop and `POST /mlops/auto-retrain` each call
  `app.promotion.gated_decision`, which adds a fourth refusal of its own -- it
  declines when the serving model's own score cannot be read.
- The decision is logged to `retrain_log`.

**Manual triggers:** `/api/v1/model/retrain`, `/api/v1/mlops/full-pipeline` (admin).

### Observation coverage

- `app/services/observation_coverage.py` owns `coverage_report` and
  `coverage_window`; the scheduler does not import HTTP authentication.
- The window is `[max(today - 30 days, M3_CLOCK_START), today)` in UTC;
  `M3_CLOCK_START` is `2026-10-11`; today is excluded as incomplete. API calls
  without `not_before` use the rolling window. A bound after today raises
  `ValueError`; a bound equal to today is an allowed empty window.
- Expected point-days are calendar dates crossed with the points due on each
  date; a LEFT JOIN gives absent observations zero hours, so missing days and
  points stay visible.
- A complete point-day has at least 19 distinct observed UTC hours. `gap_count`
  and `complete_days` count point-days; `days_examined` counts dates;
  `points_examined` counts points due in the window.
- A source/indicator without declared points raises `CoverageNotSupported`;
  unsupported input is not a report of zero gaps.
- All coverage gauges come from one report; the last-success timestamp advances
  only after success, so an old zero gauge alone does not prove the job runs.

### Models

Four, loaded in `app/main.py` at import time:

- **RandomForest** (`models/model.pkl`) -- primary ESG success predictor (9 features)
- **XGBoost** (`models/xgb_model.pkl`) -- alternative (7 features)
- **PyTorch MLP** (`models/pytorch_mlp.pth`, `SoraNet`) -- **optional; the weights
  are in neither Git nor the image**
- **Stacking Ensemble v2** (`models/ensemble_model_v2_cal.pkl`) -- calibrated
  stacking, 11 features with category/region encoding

`app.main.nn_model` is `None` unless `load_neural_network()` actually loaded
weights (a missing and an unreadable file are treated alike). It was once served
with random initialisation and averaged into blended predictions; never let a
constructed-but-untrained network count as loaded. Without the weights:

```
/predict/neural          503 {"reason_code": "neural_network_unavailable", ...}
                         -- checked before the cache
/predict/stacking        average of rf and xgb; base_models lists only those
/predict/compare         the same; the NeuralNet block is omitted, not nulled
/report/pdf              average of rf and xgb
/api/v1/ready            unaffected -- the network does not count toward readiness
/api/v1/health           checks.models.neural_network_loaded: false
analytics indicators     pytorch_mlp loaded: false
```

Making it required means training it, putting it through the same gate as the
champion and shipping the weights through LFS.

`make_features()` derives budget_per_month, co2_per_dollar, efficiency_score
plus year and quarter. SHAP's TreeExplainer is built at startup; endpoints
`/api/v1/predict/explain`, `/api/v1/explain/local`, `/api/v1/explain/global`.

### Database Schema

Twenty SQLAlchemy models in `app/database.py`, with the table each creates (most
plural, two not). Read from `__tablename__` and checked by
`tests/test_docs_name_real_tables.py`, which also refuses SQL here naming a table
no model creates. **Edit the code, then regenerate this; do not hand-edit the
table.**

| model | table |
|---|---|
| `Evaluation` | `evaluations` |
| `PredictionLog` | `predictions_log` |
| `DataRefreshLog` | `data_refresh_log` |
| `CountryIndicatorHistory` | `country_indicator_history` |
| `CountryIndicatorPeriodHistory` | `country_indicator_period_history` |
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
| `User` | `users` |
| `ProjectAccessEvent` | `project_access_events` |
| `ProjectAccessTarget` | `project_access_targets` |

There is **no** `DriftLog` model and **no** `drift_log` table: drift verdicts are
computed, returned and never persisted. The nearest durable record is
`retrain_log` -- what the closed loop decided, not what it measured.

**What `predictions_log` holds.** The table is **fed entirely by `/evaluate`**,
and only by a *save*: `POST /evaluate?save=true` writes the `evaluations` row and
its `predictions_log` row in one transaction, both owned by the caller and both
`data_scope = 'pilot'` (`app/services/evaluation_store.py`, the only writer). A
plain `POST /evaluate` writes nothing; rows from before ownership are `public`.
Two other symbols share the name and neither writes the table:

| symbol | writes | callers |
|---|---|---|
| `app.mlflow_tracking.log_prediction` | MLflow telemetry on a thread; a no-op under `SORA_OFFLINE` | `/predict`, `/predict/neural`, `/predict/stacking` -- anonymous calls only |
| `app.services.evaluation_store.save_evaluation` | the row in `predictions_log`, with its evaluation | `/evaluate?save=true`, and nothing else |
| `app.obs.request_log.log_prediction` | a JSONL file | `app/ml/routes.py`, off unless `SORA_REQUEST_LOG=1` |

So a `/predict` call leaves **no durable row**; `_log_csv` appends four input
columns to `data/predictions_log.csv`, a file destroyed on every redeploy.

- `model_version` (since 2026-10-26): `active:<run_id>`, `seed:<retrained_at>`,
  `formula:macro`, or `unknown` when model and champion differed; older rows keep
  the meaningless default `"v2.0"`.
- The row's probability is the champion's (`calculate_esg` runs
  `rf_model.predict_proba` and returns `success_probability`); the ESG **score**
  is a formula. Only `_macro_esg_from_payload` is formula-only.
- **This is what the drift check samples.** `app/api/drift.py` →
  `_recent_predictions` takes the last `window` **public** rows by id -- the scope
  filter runs before the `LIMIT` -- with no `endpoint` filter, so the KS test
  compares evaluation inputs against the baseline. An `endpoint == "predict"`
  filter would select nothing.
- Pinned by `tests/test_predictions_log_records_what_it_says.py`.

**Connection:** pgbouncer; synchronous `create_engine(pool_pre_ping=True)`,
default pool (5 + 10 overflow). No async engine.

**Accounts live in the `users` table** (`app/database.py:User`):

```
store       PostgreSQL `users`: UUID id, unique lower-case username, Argon2id hash
            (a CHECK refuses anything else; app/passwords.py), role, is_blocked,
            token_version. app/accounts.py is the only writer.
roles       admin, analyst, viewer, specialist (the pilot participant; not
            admitted by require_analyst_or_admin).
creation    administrators only: POST /api/v1/admin/users. No self-registration.
bootstrap   scripts/bootstrap_users.py (`bootstrap-users`, run by the deploy script
            after `migrate`) creates admin, analyst, viewer from
            SORA_DEFAULT_*_PASSWORD in an EMPTY table, once; otherwise it changes
            nothing and only requires an active administrator. The backend never
            reads those variables. Development: `--dev`.
sessions    JWT `sub` = account UUID, `ver` = token_version, re-read uncached on
            every request. Reset, role change, block and unblock bump the version
            and revoke every token in every worker. A token without `ver` is
            refused.
```


Code from before accounts cannot be restored once `users` exists
(`accounts_downgrade_blocked`). Fix forward; never drop the table.

**Project ownership** (`app/project_access.py`, migration `b7d41f2e9c35`).
`evaluations` and `predictions_log` carry `owner_user_id` (FK `users`,
`ON DELETE RESTRICT`) and `data_scope`, paired by a CHECK:

```
public    the shared corpus: every row from before ownership, and rows a
          maintenance script writes on purpose (it must say
          data_scope='public' -- the default is 'pilot', which needs an owner,
          so a writer that forgets is refused rather than published)
pilot     a saved evaluation; always has an owner
```

| path | anonymous | specialist | analyst, viewer | admin |
|---|---|---|---|---|
| `POST /evaluate` | computes, saves nothing | same | same | same |
| `POST /evaluate?save=true` | 401 | 201, own pilot row | 403 | 201, own pilot row |
| `GET /history`, `/history/{id}`, `/export/csv`, `/analytics/predictions-log` | 401 | own pilot rows | public rows | everything, audited |
| `DELETE /history/{id}` | 401 | 403 | 403 | that row, audited |
| `DELETE /history` | 401 | 403 | 403 | **public rows only**, audited |
| `/trends`, `/analytics/data-health` and every other shared figure | public rows | public rows | public rows | public rows |

- Somebody else's id is a 404, exactly like a missing one; a 403 means the role
  cannot do the operation and comes before any lookup. A body naming
  `owner_user_id`, `user_id` or `data_scope` is a 422. An invalid, revoked or
  blocked Bearer is a 401, never an anonymous request.
- Every shared consumer filters `data_scope = 'public'` -- a positive match --
  before its LIMIT, COUNT or sufficiency check: trends, data health, model
  health, analytics summary, admin diagnostics, drift sample, training and
  forecast loaders, LSTM status count, the scheduler's pretrain check, the
  maintenance scripts.
- `/evaluate` does not feed the MLflow evaluation experiment, the Redis drift
  observations or `evaluations_avg_score`. A signed-in call to `/predict*`,
  `/what-if`, `/evaluate/monte-carlo` or `/evaluate/ranking` uses no shared cache
  and writes no shared telemetry or CSV. A specialist gets 403 on Copilot, batch
  and `/predictions/history|export/csv`, whose storage has no owner column.
- Every administrator read or deletion of project rows is recorded in
  `project_access_events` / `project_access_targets` (`app/project_audit.py`) and
  **committed before the data is returned**; a failed audit is a 503 and no data.
  A deletion and its record are one transaction. Read it at
  `GET /api/v1/admin/project-access-log`; nothing can change it.
- `DELETE /history/{id}` leaves the evaluation's `predictions_log` row alone:
  nothing links the two reliably.

### API organization

All routes under `/api/v1/`, by domain in `app/api/`: `evaluate.py`
(`/evaluate`, `/evaluate/monte-carlo`), `predict.py` (`/predict`,
`/predict/neural`, `/predict/stacking`), `analytics.py`
(`/analytics/country-benchmark/{country}`, `/analytics/country-ranking`),
`drift.py` (`/drift/analyze`, `/drift/compare`), `retrain.py`
(`/model/retrain`), `calibration.py` (`/predict/uncertainty`,
`/calibration/reliability`), `explain.py` (`/predict/explain`, `/what-if`),
`ab_test.py` (`/ab/predict`, `/ab/stats`).

**Auth:** JWT access + refresh; login at `/api/v1/auth/login`; protected routes
need a Bearer token; admin routes check `SORA_ADMIN_TOKEN`.

### Caching

1. **Redis** (`app/redis_cache.py`): predictions, TTL 5 min, key `pred:{hash(features)}`.
2. **In-process LRU** (`app/cache.py`): country benchmarks.

`/api/v1/cache/redis/invalidate` clears the prediction cache.

## Common Development Patterns

**New endpoint:** schema in `app/schemas.py`, handler in `app/api/<domain>.py`,
register in `app/main.py` (`_all_routers` or `api_v1.include_router()`), tests in
`tests/test_<domain>.py`.

**Feature flags:** the `evaluation` table's JSONB `metadata` column
(`metadata.get("feature_enabled")`).

### Uncertainty quantification

Two routes answering two different questions:

- **`POST /api/v1/predict/uncertainty`** -- how much the RandomForest's trees
  disagree about one project. Response `UncertaintyOk`: `prediction {mean,
  median, lower_90, upper_90}` (fractions), `tree_distribution {std, n_trees,
  min, max, p5, p95, votes_for}` (p5/p95 equal lower_90/upper_90; `votes_for`
  counts trees with probability >= 0.5), `uncertainty {method, mean, std, ci_90,
  n_trees}`. Shown in `web/src/features/evaluate/UncertaintyCard.tsx`.
- **`POST /api/v1/calibration/discrepancy`** -- `rf_v1`, `stacking_v2`,
  `calibrated_v2` side by side with a weighted consensus. `near_deterministic` is
  on `calibrated_v2` only, computed as `pr_cal in (0.0, 1.0)` -- **exact** 0 or 1,
  nothing about an interval. Its "≈det" badge is in
  `web/src/features/calibration/CalibrationPage.tsx`. On an 81-project grid
  (2026-09-24) it never fired.

## Testing

`pytest.ini` gives each test **30s**; CI gives it **60s**. Run it the way CI does:

```bash
DATABASE_URL="sqlite:///./test.db" REDIS_URL="" SECRET_KEY="ci-test-secret" \
SORA_ADMIN_TOKEN="ci-test-admin" SORA_OFFLINE="1" RUN_SCHEDULER="false" \
  python -m pytest tests/ --ignore=tests/test_api.py --timeout=60 -q
```

- `--ignore` decides what runs, `--timeout` decides what passes.
  `tests/test_api.py` needs the PostgreSQL of the `integration-tests` job; it is
  run there, not unrun. Do not add `MLFLOW_TRACKING_URI`; CI does not set it.
- **Before fixing a test that failed locally, find out whether it fails in CI:**
  `gh run view <run-id> --log --job <job-id> | grep tests/test_<name>.py` -- a
  passing file prints one dot per test.
- A single test run alone is not a smaller suite run: it pays for importing
  `app.main` (torch, SHAP, transformers), which a full run has already paid. A
  test that fails alone and passes in the suite is reporting the import; run the
  file or the suite to confirm a failure.
- **Suite size:** `pytest --collect-only tests/` counts it. No figure is kept
  here: a copied count goes stale within hours.
- `tests/test_<domain>.py` mirrors `app/api/<domain>.py`; `tests/conftest.py`
  provides the TestClient and a mock session. `pytest --lf`, `pytest -x`,
  `-k "pattern"` work as usual.

## Environment Variables

Required in `.env`:

```bash
POSTGRES_PASSWORD=sora2026
SORA_ADMIN_TOKEN=<your-secret-token>
GRAFANA_PASSWORD=sora2026
SECRET_KEY=<your-jwt-secret>
DATABASE_URL=postgresql://sora:password@localhost:5432/sora_earth
REDIS_URL=redis://localhost:6379/0
RUN_SCHEDULER=false  # true only in the scheduler container
```

Optional: `SENTRY_DSN`, `MLFLOW_TRACKING_URI` (defaults to local sqlite),
`SORA_ENV`.

## Key Constraints & Gotchas

1. **Scheduler Architecture**: the scheduler runs in its own container
   (`run_scheduler.py`). Never set `RUN_SCHEDULER=true` in `app`: jobs would run
   twice.

   **Recreating the scheduler container runs five jobs immediately** -- forced by
   `modify_job(next_run_time=now)` over `app/scheduler.py:RUN_IMMEDIATELY_ON_STARTUP`,
   not an APScheduler default. So every deployment, **and a rollback**, writes to
   the database and calls external APIs. All five are deliberate (decided
   2026-09-19); the intent is that a gap in ingestion or external data starts at
   a schedule, never at a release, and a sixth entry has to meet it.

   | job | side effect | why at startup | repeating it |
   |---|---|---|---|
   | `auto_run_ingesters` | rosstat + sber rows | **decided**: the trigger is 24h, so without it the first snapshot pass after a release is up to a day away and freshness depends on when the last deploy happened | safe, measured: same revision → `inserted=0`, zero row delta |
   | `auto_refresh_external_data` | World Bank pass + one `data_refresh_log` row | **decided**: the trigger is 6h, so a release would otherwise serve up to six hours on the previous process's values; the full history pass is gated behind `SORA_HISTORY_REFRESH` *because* this runs at startup, which bounds the cost to one ordinary pass | log row appended by design; heavy history pass off by default |
   | `refresh_forecast_metrics` | reads, sets Prometheus gauges | **decided**, for the opposite reason to the others: its trigger is 30s so the gap is negligible, but it is the only entry that neither writes nor calls out, so keeping it is free. It does **not** close the 6h empty-series window — that is `auto_pretrain_forecast`, deliberately not here | safe — gauges are set, never incremented |
   | `auto_openmeteo_ingestion` | one Open-Meteo fetch, `observed` rows | **decided**: the same argument as its air-quality twin below — same source, same 1h trigger — of which only one half had been written down | **inserts, measured** — the ingester reads the API's `current` block, whose `time` moves at 15-minute resolution, so a second run 15+ minutes later writes a new row. 3620 point-hours on production hold more than one row; a startup run costs ~210 rows (21 points x 10 indicators) |
   | `auto_openmeteo_air_quality_ingestion` | one Open-Meteo fetch, `observed` rows | **stated**: otherwise the first rows arrive an hour after a deploy, and a restart to check the source shows nothing for an hour | same as above: inserts, ~126 rows per startup run (21 points x 6 indicators) |

   **For acceptance:** the listed startup jobs may write during the deployment
   window. Attribute every change through `ingester_runs`, `source` and
   `source_revision`; a deployment window is neither evidence that a write came
   from somewhere else nor a licence to accept an unexplained delta.

2. **Feature Count Consistency**: the RF model expects exactly 9 columns in this
   order: `["budget", "co2_reduction", "social_impact", "duration_months",
   "budget_per_month", "co2_per_dollar", "efficiency_score", "year", "quarter"]`.
   Always use `make_features()`.

   **Nine columns, seven of which carry information.** `year` and `quarter` are
   computed in `_do_retrain` from the retrain's own clock, identical for every
   row, so the model fits on seven. They stay because `models/model.pkl` was
   fitted with nine columns and would refuse eight: dropping them is a retrain,
   not an edit. Never read the count as nine working signals. `category`,
   `region` and `country_gdp_per_capita` in `data/projects.csv` are stored and
   not used by this model.

3. **Model Versioning**: `models/` is the **seed**, shipped in Git LFS, and in
   production it changes only by a commit. A new model is never deployed by
   replacing files there: `docker-compose.prod.yml` mounts `./models` read-only
   into `backend` and `scheduler`, and `tests/test_seed_is_immutable.py` refuses a
   write to the seed under `app/` or a writable mount.

   - **What serves.** The champion is the RandomForest set (`model.pkl`,
     `scaler.pkl`, `best_threshold.pkl`, `meta.json`, `metrics.json` when present).
     `app/model_loader.py` → `load_champion()` loads it whole from
     `runtime/active/` when complete and readable, and whole from `models/`
     otherwise -- never one file from each. `/api/v1/health` reports which under
     `model_provenance`.
   - **Which data it trained on** is `data_version` in its `meta.json` --
     `sha256:` over the bytes of `data/projects.csv` as the run read them -- beside
     `data_rows_read` and `total_samples`; the same digest is written to
     `retrain_log.data_version`. `retrain_models` and `closed_loop_retrain` each
     write a second row for the same run that carries NULL there; no open issue
     owns that.
   - **How a trained model gets there.** `app/services/retraining.py` →
     `_do_retrain()` writes the candidate to `runtime/staged/<run_id>/` (after
     `app/model_source.py` → `prune_staged()` prunes older candidates, sparing the
     one the champion came from). The only way into `runtime/active/` is
     `app/model_source.py` → `activate()`, whose one caller is
     `app/model_loader.py` → `activate_promoted_candidate()`, reached after
     `gated_decision` passes by **two** paths: `app/scheduler.py` →
     `closed_loop_retrain()` and `app/api/infra.py` → `auto_retrain_on_drift()`
     (`POST /api/v1/mlops/auto-retrain`). Every other retrain stages and stops.
   - **A process keeps the champion it loaded.** `reload_champion()` replaces it
     only in the process that activated: for scheduled jobs that is the
     `scheduler` container, so HTTP serves the new model only after the backend
     workers restart; for a route, only the worker that took the request.
   - **A change to `models/`** changes the fallback for the champion set, and is
     the only source for XGBoost, stacking and ensemble-v2. Files are read at
     process start. `deploy_production.sh` does not recreate `backend` or
     `scheduler` for a change confined to `models/` (`.dockerignore` keeps it out
     of the image): check their uptime before assuming the new files are loaded.
   - In development `docker-compose.yml` mounts `./models` writable with no
     runtime volume, so `app` never serves what `scheduler` activates.
   - Predictions are cached in Redis for `cache_set()`'s default TTL under a key
     without the model (`app/api/predict.py` → `_cache_key()`), so an answer can
     come from the previous model for that long.

4. **Database Migrations**: always create an Alembic migration for a schema
   change. `migrations/` is mounted in Docker and runs on the first `postgres`
   start.

5. **CORS Configuration**: origins are hardcoded in `app/main.py`
   (`allow_origins` of `CORSMiddleware`); add new domains there.

6. **Rate Limiting**: `SlowAPIMiddleware` (`app/rate_limit.py`) counts every
   request per caller address: 100 req/min; `/api/v1/model/retrain` also has 10
   req/min in a bucket of its own, charged **in addition to** the general one.
   Every budget is examined before any is written, so a refusal costs nothing.
   Health, readiness, metrics and favicon are exempt. The counter is per process,
   so the effective budget multiplies by the worker count: a brake on one noisy
   caller, not flood protection.

7. **Head Requests**: middleware in `app/main.py` turns HEAD into GET. Never set
   Content-Length by hand.

8. **Frontend Port**: Vite on 5173 proxies the API to 8000 (`web/vite.config.ts`).

## Monitoring & Observability

- **`/metrics`** (`app/metrics_endpoint.py`) is what `infra/prometheus.yml`
  scrapes: custom `sora_*` metrics plus HTTP instrumentation, **aggregated across
  gunicorn's four workers**. `entrypoint.sh` sets `PROMETHEUS_MULTIPROC_DIR` and
  clears it in the master before the fork. Three rules, each breaking a metric
  silently:

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

  All twelve gauges use `mostrecent`. `/api/v1/metrics/prometheus` serves the same
  registry; `/metrics/prometheus` does not exist.

- **The scheduler publishes its own** on `scheduler:9000/metrics`, its own scrape
  job. Sixteen `sora_*` metrics are written only there; derive the set rather
  than trusting a number:

  ```
  python -c "import sys; sys.path.insert(0,'tests'); \
    from test_scheduler_metrics_are_scraped import _writers_by_metric, SCHEDULER_MODULES; \
    w=_writers_by_metric(); \
    print(sorted(m for m,f in w.items() if f and all(x.startswith(SCHEDULER_MODULES) for x in f)))"
  ```

  No multiprocess directory there; `app/scheduler_metrics.py` refuses to serve if
  `PROMETHEUS_MULTIPROC_DIR` is set.

  **Four of those sixteen are absent for up to six hours after every
  deployment**, and it is not a fault: `sora_forecast_mae_current`,
  `_rmse_current`, `_r2_current`, `_mape_current` are labelled gauges set only by
  `auto_pretrain_forecast` (every 6h, not run at startup), and a restart empties
  the registry. `series=0` right after a deploy is that window, not a metric
  nobody writes; tell them apart by the deployment time or a row in
  `forecast_model_metrics`.

- `/api/v1/metrics` (JSON): request counts, uptime, response times; never scraped.
- Grafana http://localhost:3000 (admin/sora2026), dashboard "SORA MLOps Overview";
  MLflow http://localhost:5000; health `/health`, `/api/v1/health`, `/api/v1/ready`.

**Key metrics.** Checked against the code by `tests/test_key_metrics_contract.py`.

| metric | labels | written in | scrape job |
|---|---|---|---|
| `sora_predictions_total` | `model` | backend, `app/api/predict.py` | `sora-app` |
| `sora_prediction_latency_ms` | — (histogram) | backend, `app/api/predict.py` | `sora-app` |
| `sora_drift_detected_total` | — | scheduler, `app/scheduler.py` | `sora-scheduler` |
| `sora_retrain_total` | `status` | scheduler, `app/scheduler.py` | `sora-scheduler` |
| `sora_external_refresh_total` | `source`, `outcome` | scheduler, `app/scheduler.py` | `sora-scheduler` |

**Always name the job.** Every `sora_*` symbol is defined once in
`app/prom_metrics.py` and imported by both processes, so a bare name returns two
series, one permanently zero. Query `sora_drift_detected_total{job="sora-scheduler"}`
or `sum by (job) (...)`, never the bare name.

## External Dependencies

- **World Bank API** via `app/external_data.py`, cached in PostgreSQL, refreshed
  by the scheduler.
- **ChromaDB** for RAG (`app/api/rag_api.py`); optional, needs `OPENAI_API_KEY`.
- **Sentence Transformers**, loaded on demand by the RAG endpoints.

## Performance Notes

Prediction target <200ms p95. RF `predict_proba` ~5-10ms, scaling ~1ms; SHAP
`shap_values` ~50-100ms (keep it off hot paths); Redis cuts repeated-input
latency ~10x.

## Useful Debugging Commands

```bash
docker-compose logs -f scheduler | grep -E "(drift|retrain)"

# What the closed loop decided (drift verdicts are never stored).
docker-compose exec postgres psql -U sora -d sora_earth -c "SELECT started_at, status, trigger_source FROM retrain_log ORDER BY started_at DESC LIMIT 5;"

# Phase 6 forecast report; prints a refusal with reasons until 2027-04-03 and
# exits 0 either way.
python scripts/evaluate_forecast.py

curl http://localhost:8000/api/v1/cache/redis
# Rows of predictions_log -- /evaluate saves, not /predict calls.
curl http://localhost:8000/api/v1/analytics/predictions-log?limit=10
curl -X POST http://localhost:8000/api/v1/mlops/drift/observe
curl -X POST http://localhost:8000/api/v1/model/retrain -H "Authorization: Bearer $SORA_ADMIN_TOKEN"
```

## Production Server

- Host `109.120.147.12`; `ssh root@109.120.147.12`, **key only**.
- Project directory `/opt/sora_earth_ai_platform`; domain https://sora-earth.online.
- Earlier hosts `77.110.118.93` and `45.137.60.67` (deleted with all data,
  2026-08) appear in dated reports under `.claude/`; leave those alone.
- The checkout pulls over SSH with a read-only deploy key
  (`/root/.ssh/github_deploy`). On the previous host a recreated
  `runtime_models` volume needed `chown 1000:1000` for the activation lock; not
  yet checked here.
- The host runs Docker 29, which writes some errors in lower case (`error: no such
  object`). A guard that parses Docker's text must accept both spellings
  (`docs/incidents/2026-11-05-ownership-cutover-docker29.md`).

**Deployment — one supported way, and this is it:**

```bash
cd /opt/sora_earth_ai_platform
git pull --ff-only origin main      # the script never pulls; it requires HEAD == origin/main
./scripts/deploy_production.sh
```

Rollback is the same script: `./scripts/deploy_production.sh --rollback SHA`
(only an ancestor of `origin/main`).

- **A commit carrying `.deploy-blocked` is not deployable, by either mode.** The
  script reads it from the target commit before checkout, build, migration or any
  container change, and refuses with its first line; there is no override. **A
  marker is removed only by the change that delivers what it holds back.** The
  project-ownership marker was removed that way; its evidence and the
  deployment-day conditions are in `docs/OWNERSHIP_CUTOVER_RELEASE.md`.
- **A manual `docker compose up` / `build` / `restart` is not a deployment
  procedure, and
  a green container health check is not evidence that the site works.** The
  script recreates nginx *after* the backend, runs `nginx -t`, checks the
  upstream and the certificate store, checks `https://sora-earth.online/health`
  from outside, and requires `/api/v1/health` to report the database healthy. A
  hand recreate of the backend once left nginx on a stale address and the site
  502 while every container reported healthy; if you recreate a container by
  hand, reload nginx and check the public endpoint.
- **Two configurations are bind-mounted single files, and a container pins the
  inode it started with:** `nginx/nginx.conf` and `infra/prometheus.yml`. A `git
  pull` does not reach the running process. The script recreates both and reads
  each configuration back out of its container. Prometheus runs without
  `--web.enable-lifecycle` (`POST /-/reload` is 403). A prometheus failure warns
  and does not roll back; the last step asks prometheus which jobs it scrapes
  (`promtool query instant ... up`).
- Run it detached from the SSH session (`setsid`, stdin from `/dev/null`) and keep
  its real exit code: a successful `nohup` is not a successful deployment.

**The cutover.** The script builds the image once and **pins** `migrate`,
`bootstrap-users`, `backend` and `scheduler` to its id, then waits for PostgreSQL;
a failure up to here refuses with exit 1 and changes nothing that serves. From
the first stop on, the run is `mutating`, journalled, and a refusal goes through
the rollback:

```
stop nginx -> stop -t 60 backend scheduler -> confirm every container stopped
-> wait for open transactions to end -> one named migrator, under a deadline
-> bootstrap-users -> start dependencies with --no-deps
-> force-recreate backend and scheduler -> check images and schema head
-> the remaining services, nginx, prometheus -> health checks -> manifest
```

The old backend and scheduler are stopped **before** the migration: old code
beside or after the ownership migration is a mixed rollout the schema refuses.

**After a refusal:**

- Rollback allowed: the previous images are restored by id, nginx included; exit 1.
- Rollback barred, or nothing to restore: nothing old is started; exit 76 with
  `rollback-refused` (or `rollback-impossible`) in the journal. If the refusal
  came between the stop and a verified start, backend and scheduler are
  **stopped and the site is not serving**.
- `STOPPED` is printed only when docker confirms every backend and scheduler
  container stopped; otherwise the journal's `apps` line reads `unknown` --
  inspect with `docker compose -p sora_earth_ai_platform ps -a` first.
- After `apps started`, a barred rollback leaves the new code running.
- The pending revisions run in one transaction, so the schema is migrated whole or
  not at all; the barriers read the catalogue.
- **Fix forward** with a corrected release through the script; restoring a backup
  is a separate emergency operation (`docs/BACKUP_RESTORE.md`). **Do not start
  containers by hand.** To deploy over a refused run, save the journal, confirm no
  deploy process and no migrator remain, then
  `rm /var/lib/sora/deployments/in-progress` (`docs/DEPLOY.md`).

**Budgets** -- each ends in a refusal, not a wait without end; conservative bounds,
not measured: `DEPLOY_STOP_TIMEOUT=60`, `DEPLOY_DRAIN_SECONDS=30`,
`DEPLOY_MIGRATE_DEADLINE=120`, `DEPLOY_MIGRATOR_GONE_SECONDS=30`,
`DEPLOY_MIGRATOR_CREATE_SECONDS=60` (the creating client is never killed; past
this the migrator's existence is unknown and every rollback is forbidden, exit
76). An override must be a whole number without leading zeros.
`scripts/rehearse_ownership_cutover.sh` measures the window on a restored
production dump and refuses one over 300 s (docs/DEPLOY.md, "Rehearsing the
cutover").

**Rollback barriers** -- asked before checkout, image restore or fallback build,
for the explicit and the automatic rollback alike:

1. **Accounts** -- the target predates persistent accounts and `users` exists.
2. **Ownership** -- the target lacks any of `app/project_access.py`,
   `app/services/evaluation_store.py` or the ownership migration, and the
   catalogue has any of `evaluations.owner_user_id`, `evaluations.data_scope`,
   `predictions_log.owner_user_id`, `predictions_log.data_scope`,
   `project_access_events`, `project_access_targets`.

A barrier allows only on an exact answer from a psql that exited 0
(`users:false`, `ownership:0`). Before the automatic rollback reads the schema,
the migrator must be confirmed gone positively (its client returned on its own,
its container reported as no longer existing, its session terminated). Explicit
`--rollback` barred: exit 1 before anything changes. Automatic: exit 76,
`rollback-refused`. Schema downgrades are unsupported; every commit from before
project ownership is now refused as a rollback target. The success record's "to
undo" line asks the same barriers and says "no rollback ... fix forward" when one
refuses.

**Production containers (9):** backend, scheduler, nginx, postgres, pgbouncer,
redis, mlflow, prometheus, grafana. Only 80/443 are exposed (nginx); Grafana and
Prometheus bind `127.0.0.1`; TLS by nginx from `/etc/letsencrypt/`.

### Working protocol (CRITICAL)

- Before any patch: `git --no-pager diff`, and validate Python with
  `python3 -c "import ast; ast.parse(open('path/to/file.py').read())"`.
- Multi-line patches through a Python heredoc that replaces on unique anchors and
  ends with `ast.parse(content)`; never `sed -i` on Python. If an anchor is not
  found, the file must be left unmodified.
- Always `git --no-pager` over SSH (a pager hangs the session).

### SSH configuration

```
Host sora
    HostName 109.120.147.12
    User root
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
    ServerAliveInterval 60
    ServerAliveCountMax 3
```

### Common production operations

```bash
docker compose -f docker-compose.prod.yml logs -f backend
docker compose -f docker-compose.prod.yml logs -f scheduler
# A restart is not a deployment; it only bounces the current image.
docker compose -f docker-compose.prod.yml restart backend
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml exec postgres \
  pg_dump -U sora sora_earth > backup_$(date +%Y%m%d_%H%M%S).sql
docker compose -f docker-compose.prod.yml exec postgres \
  psql -U sora -d sora_earth -c \
  "SELECT started_at, status, trigger_source FROM retrain_log ORDER BY started_at DESC LIMIT 5;"
```

Grafana and Prometheus need a tunnel: `ssh -L 3000:127.0.0.1:3000 root@109.120.147.12`,
`ssh -L 9090:127.0.0.1:9090 root@109.120.147.12`. Health:
`curl https://sora-earth.online/health`.
