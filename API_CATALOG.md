# SORA.Earth — API Catalog

**Total endpoints:** 127  
**Categories:** 23  
**Generated:** 2026-05-05 01:10 (auto from FastAPI OpenAPI spec + path-based classifier)  
**Production:** https://api.sora-earth.ru

## Summary by category

| Category | Endpoints |
|----------|-----------|
| **mlops** | 20 |
| **analytics** | 12 |
| **system** | 11 |
| **prediction** | 9 |
| **infrastructure** | 8 |
| **data-pipeline** | 8 |
| **admin** | 8 |
| **explainability** | 7 |
| **auth** | 6 |
| **history** | 5 |
| **calibration** | 5 |
| **ai-control** | 4 |
| **batch** | 3 |
| **a/b-testing** | 3 |
| **drift** | 3 |
| **compliance** | 3 |
| **reporting** | 2 |
| **cache** | 2 |
| **monitoring** | 2 |
| **ab-comparison** | 2 |
| **ai-teammate** | 2 |
| **websocket** | 1 |
| **mlflow** | 1 |

---

## a/b-testing (3)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/ab/predict` | Ab Predict |
| `POST` | `/api/v1/ab/split` | Set Split |
| `GET` | `/api/v1/ab/stats` | Ab Stats |

## ab-comparison (2)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/model/ab-comparison` | Ab Comparison |
| `GET` | `/api/v1/model/ab-comparison/plot` | Ab Comparison Plot |

## admin (8)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin` | Admin Dashboard |
| `GET` | `/api/v1/admin/diagnostics` | Admin Diagnostics |
| `GET` | `/api/v1/admin/retrain-log` | List Retrain Log |
| `GET` | `/api/v1/admin/snapshot` | Get Admin Snapshot |
| `GET` | `/api/v1/admin/stats` | Admin Stats |
| `GET` | `/api/v1/admin/timeline` | Admin Timeline |
| `GET` | `/api/v1/admin/users` | List Users |
| `GET` | `/api/v1/audit/log` | Get Audit |

## ai-control (4)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/admin/ai/full-pipeline` | Ai Trigger Full Pipeline |
| `POST` | `/api/v1/admin/ai/refresh` | Ai Trigger Refresh |
| `POST` | `/api/v1/admin/ai/report` | Ai Generate Report |
| `POST` | `/api/v1/admin/ai/retrain` | Ai Trigger Retrain |

## ai-teammate (2)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/admin/ai-teammate/run` | Run Teammate |
| `GET` | `/api/v1/admin/ai-teammate/status` | Teammate Status |

## analytics (12)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/analytics/country-benchmark/{country}` | ESG benchmark data for a country |
| `GET` | `/api/v1/analytics/country-ranking` | Global ESG ranking with pagination |
| `GET` | `/api/v1/analytics/data-health` | Data Health |
| `GET` | `/api/v1/analytics/metrics/model-health` | Model Health |
| `POST` | `/api/v1/analytics/model-compare` | Compare all ML models on a project |
| `POST` | `/api/v1/analytics/monte-carlo` | Monte Carlo risk simulation |
| `GET` | `/api/v1/analytics/predictions-log` | Get Predictions Log |
| `GET` | `/api/v1/analytics/summary` | Analytics Summary |
| `GET` | `/api/v1/countries` | Countries List |
| `POST` | `/api/v1/ghg-calculate` | Ghg Calculate |
| `GET` | `/api/v1/regions` | Regions |
| `GET` | `/api/v1/trends` | Trends |

## auth (6)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/auth/login` | Login |
| `POST` | `/api/v1/auth/login-json` | Login Json |
| `GET` | `/api/v1/auth/me` | Get Me |
| `POST` | `/api/v1/auth/refresh` | Refresh Token |
| `POST` | `/api/v1/auth/register` | Register User |
| `GET` | `/api/v1/auth/verify` | Verify Key |

## batch (3)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/batch` | List Batches |
| `POST` | `/api/v1/batch/evaluate` | Batch Evaluate |
| `GET` | `/api/v1/batch/{batch_id}` | Get Batch |

## cache (2)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/cache/clear` | Clear Cache |
| `GET` | `/api/v1/cache/stats` | Cache Stats |

## calibration (5)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/calibration/brier` | Calibration Brier |
| `POST` | `/api/v1/calibration/discrepancy` | Calibration Discrepancy |
| `POST` | `/api/v1/calibration/reliability` | Calibration Reliability |
| `GET` | `/api/v1/model/reliability-diagram` | Reliability Diagram |
| `POST` | `/api/v1/predict/uncertainty` | Predict With Uncertainty |

## compliance (3)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/compliance/csrd` | Csrd Check |
| `GET` | `/api/v1/compliance/frameworks` | List Frameworks |
| `POST` | `/api/v1/compliance/gap-analysis` | Gap Analysis |

## data-pipeline (8)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/data/countries` | All Countries |
| `GET` | `/api/v1/data/countries/supported` | Supported Countries |
| `GET` | `/api/v1/data/country/{name}` | Single Country |
| `POST` | `/api/v1/data/refresh` | Refresh Data |
| `GET` | `/api/v1/data/refresh-status` | Refresh Job Status |
| `GET` | `/api/v1/data/refresh/logs` | Refresh Logs |
| `GET` | `/api/v1/data/refresh/status` | Refresh Job Status |
| `GET` | `/api/v1/data/status` | Data Status |

## drift (3)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/drift/analyze` | Analyze data drift: training vs recent predictions |
| `POST` | `/api/v1/drift/compare` | Compare two time periods for drift |
| `GET` | `/api/v1/drift/features/stats` | Current feature statistics |

## explainability (7)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/explain/beeswarm` | Beeswarm Plot |
| `GET` | `/api/v1/explain/global` | Explain Global |
| `POST` | `/api/v1/explain/local` | Explain Local |
| `POST` | `/api/v1/predict/explain` | Explain Prediction |
| `POST` | `/api/v1/predict/explain/waterfall` | Explain Waterfall |
| `POST` | `/api/v1/shap` | Shap Explain |
| `POST` | `/api/v1/what-if` | What If |

## history (5)

| Method | Path | Description |
|--------|------|-------------|
| `DELETE` | `/api/v1/history` | Clear History |
| `GET` | `/api/v1/history` | Get History |
| `DELETE` | `/api/v1/history/{eval_id}` | Delete Evaluation |
| `GET` | `/api/v1/history/{eval_id}` | Get Evaluation By Id |
| `GET` | `/api/v1/scheduler/retrain/history` | Retrain history log |

## infrastructure (8)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/cache/redis` | Redis cache stats |
| `DELETE` | `/api/v1/cache/redis/invalidate` | Invalidate all prediction cache |
| `DELETE` | `/api/v1/cache/redis/invalidate/{prefix}` | Invalidate cache by prefix |
| `GET` | `/api/v1/cache/redis/test` | Test Redis cache |
| `GET` | `/api/v1/infra/data-refresh-status` | Data Refresh Status |
| `POST` | `/api/v1/infra/data-refresh/run` | Data Refresh Run |
| `POST` | `/api/v1/mlops/auto-retrain` | Auto Retrain On Drift |
| `POST` | `/api/v1/mlops/full-pipeline` | Run Full Pipeline |

## mlflow (1)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/mlflow/stats` | Mlflow Stats |

## mlops (20)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/mlops/drift` | Check Drift Infra |
| `DELETE` | `/api/v1/mlops/drift/baseline` | Reset Baseline |
| `GET` | `/api/v1/mlops/drift/baseline` | Baseline Status |
| `POST` | `/api/v1/mlops/drift/baseline/fit` | Fit Baseline |
| `POST` | `/api/v1/mlops/drift/observe` | Observe |
| `POST` | `/api/v1/mlops/drift/simulate` | Simulate Drift |
| `GET` | `/api/v1/mlops/health` | Mlops Health |
| `GET` | `/api/v1/model/compare` | Compare Models |
| `POST` | `/api/v1/model/data/bulk-upload` | Data Bulk Upload |
| `POST` | `/api/v1/model/data/refresh` | Data Refresh |
| `GET` | `/api/v1/model/drift` | Check Drift |
| `GET` | `/api/v1/model/drift/mlflow-history` | Drift Mlflow History |
| `GET` | `/api/v1/model/feature-importance` | Feature Importance |
| `GET` | `/api/v1/model/metrics` | Model Metrics |
| `GET` | `/api/v1/model/prediction-log/stats` | Prediction Log Stats |
| `POST` | `/api/v1/model/retrain` | Retrain Model |
| `GET` | `/api/v1/model/status` | Model Status |
| `POST` | `/api/v1/scheduler/refresh_external` | Trigger external ESG data refresh now |
| `POST` | `/api/v1/scheduler/retrain/trigger` | Trigger manual retrain now |
| `GET` | `/api/v1/scheduler/status` | Scheduler status and jobs |

## monitoring (2)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/rate-limit/status` | Rate Limit Status |
| `GET` | `/system/health` | System Health |

## prediction (9)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/evaluate` | Evaluate Project |
| `POST` | `/api/v1/evaluate/monte-carlo` | Evaluate Monte Carlo |
| `POST` | `/api/v1/evaluate/ranking` | Evaluate Ranking |
| `POST` | `/api/v1/predict` | Predict Project |
| `POST` | `/api/v1/predict/compare` | Predict Compare |
| `POST` | `/api/v1/predict/neural` | Predict Neural |
| `POST` | `/api/v1/predict/stacking` | Predict Stacking |
| `GET` | `/api/v1/predictions/export/csv` | Export Predictions Csv |
| `GET` | `/api/v1/predictions/history` | Predictions History |

## reporting (2)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/export/csv` | Export Csv |
| `POST` | `/api/v1/report/pdf` | Generate Pdf Report |

## system (11)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Read Root |
| `GET` | `/api/v1/health` | Full health check |
| `GET` | `/api/v1/metrics` | Get Metrics |
| `GET` | `/api/v1/metrics/prometheus` | Prometheus Metrics |
| `GET` | `/api/v1/ready` | Readiness probe |
| `GET` | `/api/v1/system/metrics` | Get System Metrics |
| `GET` | `/dev` | Dev Page |
| `GET` | `/health` | Health |
| `GET` | `/metrics` | Metrics |
| `GET` | `/model-info` | Model Info |
| `GET` | `/model-metrics` | Get Model Metrics |

## websocket (1)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/ws/status` | Ws Status |