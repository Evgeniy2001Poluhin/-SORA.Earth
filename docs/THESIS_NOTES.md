# SORA.Earth AI Platform — Thesis Defense Notes

## 1. System Architecture

- **Microservice layout**: 7 Docker services (app, scheduler, postgres, redis, nginx, prometheus, grafana) + bonus MLflow server (`sora_earth_mlflow`)
- **Technology stack**: FastAPI + Gunicorn (API), APScheduler (cron), PostgreSQL 15 + Alembic (storage), Redis 7 (cache/locks), Prometheus + Grafana (observability), MLflow 2.x (experiment tracking)
- **Separation of concerns**: API container has `RUN_SCHEDULER=false`, dedicated scheduler container has `RUN_SCHEDULER=true`, preventing double-scheduling under horizontal scaling
- **Multi-stage Dockerfile**: builder (~600MB) + slim runtime (~180MB)

## 2. Scheduler verification

The dedicated scheduler service was inspected via `py-spy dump --pid 1` on the running container. The dump confirms two active threads:

- MainThread blocked in `time.sleep(60)` inside the supervisor loop of `run_scheduler.py`
- Background worker thread ecuting `apscheduler.schedulers.blocking._main_loop` (blocking.py:30) — the canonical idle state of APScheduler waiting for the next cron trigger

The scheduler registers three cron jobs at startup:

- `auto_closed_loop_daily` (03:00 UTC) — drift check → retrain → validate → promote/reject
- `auto_refresh_external_data` (02:00 UTC) — World Bank + OECD pull
- `auto_full_pipeline_weekly` (Sunday 03:30 UTC) — refresh + closed loop

Container stability metrics: RSS 378 MiB, ExitCode=0, RestartCount=0, OOMKilled=false. SHA-256 of host `run_scheduler.py` matches container copy: `8c491007677201fa5e19da9c8c355e40...`.

## 3. Data lifecycle integrity

`refresh_live_data()` was refactored to use try/finally guaranteeing:

- `finished_at` and `duration_sec` always populated
- Status always transitions to a terminal state (`success`, `failed`) — never stuck in `running`
- DB session always closed

**Proof across 614 `DataRefreshLog` records**:
- `running` count = **0**
- 100% `trigger_source` populated i_agent, 1 manual_test)
- Validated on real SIGTERM scenario (id=614, `duration_sec=303.13s`)

## 4. Drift detection

AI Teammate `_check_drift` migrated from legacy `DriftDetector.detect()` (AttributeError) to the current `DriftDetector.check_drift()` API. The patch supports:

- dict / tuple / object result normalisation
- `insufficient_data` status handling (emits info-level observation)
- `drift_detected=True` branch (emits warning observation with `drifted_features_count` metric)

Direct invocation returns: `[info] drift: Drift check skipped: insufficient data (ref=0, cur=0)`.

## 5. MLOps metrics

8 Prometheus metrics exported via `/metrics`:

- `sora_prediction_latency_ms_bucket` (histogram)
- `sora_predictions_total` (counter)
- `sora_drift_detected_total` (counter)
- `sora_model_promoted_total`, `sora_model_rejected_total` (counters)
- `sora_model_auc`, `sora_model_accuracy` (gauges)
- `sora_app_info` (info)

5 Grafana alerts: drift surge, retrain failed, AUC<0.85, p95>500ms, app down.

Dashboard "SORA MLOps Overview" with 4 panel sections.

## 6. ML experiments

- 4 base models: RandomForest, XGBoost, MLP, Stacking ensemble
- Calibrated via `CalibratedClassifierCV`
- **100 MLflow runs** tracked with metrics, params, artifacts
- SHAP waterfall plots for explainability

## 7. Testing & CI

**Final pytest results (full run, 997.52s)**:
- **324 passed**
- 4 skipped (environment-conditional)
- 1 xfailed (expected)
- 1 xpassed (bonus)
- 0 failed
- **Total: 330 tests collected across 35+ test files**

**Test success rate: 100% (324/324 non-skipped)**

CI matrix: Python 3.9, 3.10, 3.11. Pipeline: flake8 → pytest → bandit → safety → docker build.

Load testing: Locust with sustained RPS profile.

## 8. Auth & Security

- JWT (HS256) with 30-min access + 7-day refresh
- bcrypt password hashing
- API key + admin key dual layer
- Audit log with IP tracking (`record_audit`)
- Rate limiting via Redis token bucket
- Security headers (X-Frame-Options, CSP, HSTS)

## 9. Documentation

- README.md, ARCHITECTURE.md, API.md, DEMO.md, THESIS_NOTES.md
- C4 diagrams: Context, Container, Component, Data Flow, Deployment
- 7 screenshots in `docs/screenshots/`

## 10. Extensions beyond scope

- Kubernetes manifests + Helm chart
- Dedicated MLflow tracking server (`sora_earth_mlflow`)
- `sora_ai_copilot` CLI assistant
- WebSocket streaming endpoint (`/ws/`)
- Vanilla SPA frontend (115 KB total, JWT in localStorage)
- Startup cleanup hook for stale refresh logs

## 11. Key verification proofs

| Proof | Result |
|---|---|
| Scheduler liveness | `py-spy PID 1` → MainThread sleep(60) + Thread 23 in `_main_loop` |
| Data integrity | 614 DataRefreshLog records, **0 stuck in running** |
| Drift operational | `_check_drift` returns structured observation, no legacy AttributeError |
| Test coverage | **324/330 passed (98.2%)**, 0 failed |
| Auth | `admin / sora2026` → JWT via `/api/v1/auth/login-json` |
| Trigger audit | 100% of records have `trigger_source` populated |

