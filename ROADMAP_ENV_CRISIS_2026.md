# SORA.Earth Environmental Intelligence Platform

**Roadmap:** Environmental Forecasting & Early Crisis Warning  
**Version:** 1.0  
**Date:** 2026-07-17  
**Status:** Planned implementation  
**Production:** 45.137.60.67  
**Primary objective:** прогнозирование экологических показателей и раннее предупреждение экологических кризисов.

---

## 1. Product Vision

SORA.Earth должна стать платформой экологической аналитики, которая:

1. Собирает исторические и оперативные экологические данные.
2. Контролирует качество, свежесть и происхождение каждого наблюдения.
3. Строит point-in-time-correct признаки без утечек из будущего.
4. Прогнозирует ключевые экологические показатели на горизонте до 72 часов.
5. Оценивает вероятность опасных экологических событий.
6. Учитывает exposure, vulnerability и severity.
7. Присваивает regional crisis risk levels.
8. Предоставляет uncertainty, evidence и explainability.
9. Передаёт критические решения специалисту.
9. Сохраняет полный audit trail.
10. Использует фактические результаты для последующего переобучения.

SORA.Earth не заменяет метеорологические и кризисные службы. Платформа является decision-support system и research platform.

---

## 2. Initial Scope

### Pilot geography

Первая версия работает на существующей карте регионов России.

Не начинать сразу со всех стран БРИКС. Архитектура должна поддерживать добавление новых стран и регионов без переписывания моделей и базы данных.

### MVP hazards

Первая версия охватывает:

1. Загрязнение воздуха.
2. Экстремальную жару.

После MVP с подтверждёнными метриками:

3. Риски пожаров.
4. Риски засухи.
5. Риски паводков.
6. Комбинированные риски.

---

## 3. High-Level Data Flow

```text
        External Environmental APIs
                 |
                 v
        Environmental Ingesters
                 |
                 v
     Raw observations + metadata
                 |
                 v
       Data Quality Validation
                 |
                 v
  Point-in-Time Environmental Features
                 |
                 v
        Forecasting Model Layer
                 |
                 v
     Anomaly and Hazard Detection
                 |
                 v
 Exposure + Vulnerability + Severity
                 |
                 v
        Crisis Risk Engine
                 |
                 v
 Green / Yellow / Orange / Red Alert
                 |
                 v
 API + Map + Grafana + Notifications
                 |
                 v
 Expert review + observed outcome
                 |
                 v
       MLOps retraining feedback
```

---

## 4. Engineering Principles

1. Audit before implementation.
2. Reuse existing architecture and conventions.
3. Do not break existing ESG endpoints.
4. Do not duplicate scheduler, database, metrics or MLOps services.
5. Use Alembic for every schema change.
6. Every new module requires unit and integration tests.
7. External API calls must use timeout, retry, cache and fallback.
8. No synthetic data may be written to production tables.
9. Synthetic data are allowed only in isolated tests and benchmarks.
10. All timestamps must be timezone-aware and stored in UTC.
11. Separate event time, publication time and ingestion time.
12. Do not use random train/test split for time-series evaluation.
13. Deep-learning models must not become champion based only on sample count.
14. Every model must beat a simple baseline.
15. Critical alerts require uncertainty and human review.
16. No deployment before tests and migration checks pass.
17. Never modify secrets or commit `.env`.
18. Make small, reviewable commits.
19. Update roadmap status after every completed task.
20. Report facts only; do not claim metrics that were not measured.

---

## 5. Target Project Structure

Claude Code must first inspect the actual repository and adapt names to current conventions. Do not create duplicates if equivalent modules already exist.

Suggested structure:

```text
app/
├── api/
│   ├── environmental.py
│   └── crisis_alerts.py
├── ingesters/
│   ├── openmeteo.py
│   ├── openaq.py
│   ├── copernicus.py
│   └── nasa_firms.py
├── models/
│   ├── environmental_observation.py
│   ├── environmental_forecast.py
│   ├── crisis_event.py
│   └── crisis_alert.py
├── schemas/
│   ├── environmental.py
│   └── crisis_alerts.py
├── services/
│   ├── environmental/
│   │   ├── data_quality.py
│   │   ├── feature_builder.py
│   │   ├── baseline.py
│   │   ├── catboost_forecaster.py
│   │   ├── backtesting.py
│   │   ├── calibration.py
│   │   └── uncertainty.py
│   └── crisis/
│       ├── hazard_detection.py
│       ├── exposure.py
│       ├── vulnerability.py
│       ├── severity.py
│       ├── risk_engine.py
│       └── alert_lifecycle.py

tests/
├── environmental/
├── crisis/
└── integration/

docs/
├── ENVIRONMENTAL_ARCHITECTURE.md
├── DATA_DICTIONARY.md
├── MODEL_CARD_ENVIRONMENTAL.md
├── MODEL_CARD_CRISIS.md
└── CONFERENCE_EVIDENCE.md
```

---

# PHASE 0 — Repository Audit and Baseline

**Duration:** 2–4 days  
**Priority:** Critical  
**Code changes:** Minimal, documentation and diagnostics first.

## Objectives

Understand the actual repository before changing it and establish honest production baselines.

## Tasks

- [ ] Inspect repository structure.
- [ ] Read `README.md`, current roadmap and deployment documentation.
- [ ] Inspect FastAPI router registration.
- [ ] Inspect SQLAlchemy models and Alembic migrations.
- [ ] Inspect scheduler jobs and Redis locks.
- [ ] Inspect existing ingester interface.
- [ ] Inspect forecasting services and model registry.
- [ ] Inspect MLflow integration.
- [ ] Inspect Prometheus metrics implementation.
- [ ] Inspect existing Grafana provisioning.
- [ ] Inspect all forecast-related tests.
- [ ] Run the full local test suite.
- [ ] Run frontend type-check and production build.
- [ ] Record current production API endpoints.
- [ ] Verify `/health`, `/ready` and metrics endpoint.
- [ ] Verify current Prophet, ensemble and LSTM state.
- [ ] Verify whether forecast metrics exist in Prometheus.
- [ ] Verify that the Grafana forecasting dashboard is provisioned.
- [ ] Measure real MAE, RMSE, MASE and latency.
- [ ] Determine whether future-data leakage exists.
- [ ] Create `docs/ENVIRONMENTAL_BASELINE_AUDIT.md`.

## Baseline report must include

- Current data sources.
- Number of real observations.
- Date range.
- Sampling frequency.
- Missing-value percentage.
- Duplicate percentage.
- Current models.
- Current training procedure.
- Current validation procedure.
- Real production metrics.
- Known leakage risks.
- Known deployment defects.
- Recommended implementation sequence.

## Exit criteria

- Full baseline report exists.
- Tests have been run and results are measured, not assumed.
- No new environmental architecture is implemented before audit approval.

---

# PHASE 1 — Environmental Data Foundation

**Duration:** 2–3 weeks  
**Priority:** Critical

## 1.1 Database schema

Create normalized database models and Alembic migrations.

### Environmental observation

Required fields:

```text
id
region_id
country_code
latitude
longitude
indicator
value
unit
source
source_record_id
event_time
published_at
ingested_at
source_revision
quality_score
is_valid
metadata_json
created_at
updated_at
```

### Data-source health

Required fields:

```text
id
source
checked_at
status
latency_ms
records_received
records_rejected
freshness_seconds
error_type
error_message
```

### Constraints

- Unique constraint preventing duplicate source observations.
- Index on region, indicator and event time.
- Index on source and ingestion time.
- UTC-aware timestamps.
- JSON metadata must not replace structured critical fields.

## 1.2 Data sources

Initial integration order:

1. OpenAQ for air-quality observations.
2. Open-Meteo for weather and forecast regressors.
3. ERA5 or Copernicus historical climate data.
4. NASA FIRMS after air/heat MVP.
5. Additional national sources after baseline stability.

Each ingester must implement:

```python
async def fetch(...) -> list[RawObservation]
def normalize(raw) -> EnvironmentalObservationInput
def validate(observation) -> ValidationResult
async def persist(observations) -> IngestionReport
```

## 1.3 Data quality

Implement the following checks:

- Schema validity.
- Required fields.
- Physical ranges.
- Missing values.
- Duplicate observations.
- Stale data.
- Timestamp consistency.
- Sudden impossible jumps.
- Geographic validity.
- Source availability.
- Distribution drift.
- Cross-source disagreement.

Data-quality report must produce:

```text
quality_score: 0..100
records_received
records_accepted
records_rejected
freshness_seconds
missing_rate
duplicate_rate
outlier_rate
warnings
errors
```

## 1.4 Scheduler

Suggested schedules:

- OpenAQ: every 1 hour.
- Weather forecast: every 1 hour.
- Historical climate archive: daily.
- Source-health check: every 15 minutes.
- Data-quality aggregation: every 1 hour.

All jobs require:

- Redis distributed lock.
- Retry with exponential backoff.
- Timeout.
- Structured logging.
- Metrics.
- Idempotency.
- Database execution log.

## 1.5 API

Create endpoints:

```text
GET  /api/v1/environmental/indicators
GET  /api/v1/environmental/observations
GET  /api/v1/environmental/regions/{region_id}/latest
GET  /api/v1/environmental/regions/{region_id}/history
GET  /api/v1/environmental/data-quality
GET  /api/v1/environmental/sources/health
POST /api/v1/admin/environmental/refresh
```

## Exit criteria

- At least two real data sources are integrated.
- Idempotent refresh works.
- Invalid observations are rejected or quarantined.
- Data freshness is visible.
- Migrations pass on empty and existing databases.
- New endpoints are tested.
- Existing ESG APIs remain compatible.

---

# PHASE 2 — Forecasting MVP

**Duration:** 3–4 weeks  
**Priority:** Critical

## 2.1 Indicators

First models:

- PM2.5.
- PM10 if sufficient data exist.
- Temperature.
- Heat index.

## 2.2 Baselines

Implement mandatory baselines:

- Last-value naive.
- Seasonal naive.
- Moving average.
- ETS or SARIMAX where applicable.

No advanced model may be promoted unless it beats the relevant baseline.

## 2.3 Feature engineering

Create point-in-time-correct features:

- Hour of day.
- Day of week.
- Month.
- Season.
- Lag 1, 3, 6, 12, 24 and 48.
- Rolling mean.
- Rolling standard deviation.
- Rolling minimum and maximum.
- Temperature.
- Humidity.
- Wind speed and direction.
- Pressure.
- Precipitation.
- Region metadata.
- Population and vulnerability features where appropriate.

All rolling and lag features must use past information only.

## 2.4 Candidate models

Implementation order:

1. Baselines.
2. Existing Prophet.
3. SARIMAX/ETS.
4. CatBoost or LightGBM.
5. Ensemble.
6. Deep-learning challenger only after sufficient data exist.

LSTM must remain in shadow mode until it:

- Has sufficient real history.
- Passes temporal backtesting.
- Beats seasonal naive.
- Beats or complements the current ensemble.
- Has acceptable latency and calibration.

## 2.5 Temporal backtesting

Implement rolling-origin evaluation:

```text
Train window 1 -> validation horizon 1
Train window 2 -> validation horizon 2
Train window 3 -> validation horizon 3
...
```

Evaluate separately for:

- 6 hours.
- 24 hours.
- 48 hours.
- 72 hours.
- Each indicator.
- Each region.
- Normal and crisis periods.

Required metrics:

- MAE.
- RMSE.
- MASE.
- WAPE.
- Prediction interval coverage.
- Prediction interval width.
- Inference latency.
- Sample count.

## 2.6 Uncertainty

Every forecast must return:

```json
{
  "value": 42.1,
  "lower": 34.0,
  "upper": 51.7,
  "confidence": 0.87,
  "horizon_hours": 24,
  "model_version": "environmental-pm25-v1",
  "data_freshness_seconds": 930
}
```

Use quantile regression, conformal prediction or another validated interval method.

## 2.7 Forecast API

Create:

```text
GET  /api/v1/environmental/forecast
GET  /api/v1/environmental/forecast/compare
GET  /api/v1/environmental/forecast/performance
GET  /api/v1/environmental/forecast/latest
POST /api/v1/admin/environmental/forecast/retrain
```

## Exit criteria

- Two environmental indicators have production-capable forecasts.
- Rolling backtesting is reproducible.
- Forecasts include intervals.
- Champion model beats seasonal naive.
- Results are recorded in MLflow.
- No model is promoted based on one split or synthetic data.

---

# PHASE 3 — Crisis Risk Engine

**Duration:** 3–4 weeks  
**Priority:** High

## 3.1 Crisis taxonomy

Define machine-readable event types:

```text
AIR_POLLUTION_EPISODE
EXTREME_HEAT
WILDFIRE_RISK
DROUGHT_RISK
FLOOD_RISK
WATER_SHORTAGE
COMPOUND_EVENT
```

For every type define:

- Indicator.
- Threshold source.
- Minimum duration.
- Geographic scope.
- Warning lead time.
- Cancellation condition.
- Severity levels.
- Required confidence.
- Expert-review policy.

Thresholds must be configurable and versioned, not hard-coded throughout business logic.

## 3.2 Risk formula

Initial risk score:

```text
risk = hazard_probability
       * exposure_score
       * vulnerability_score
       * severity_score
```

Normalize to 0–100.

Suggested alert levels:

```text
0–24   Green
25–49  Yellow
50–74  Orange
75–100 Red
```

These initial boundaries are configuration defaults and must later be calibrated against historical outcomes.

## 3.3 Components

### Hazard probability

Probability that an environmental indicator crosses the defined hazardous level within the selected horizon.

### Exposure

- Population.
- Settlements.
- Hospitals.
- Schools.
- Critical infrastructure.
- Agricultural area.
- ESG projects under exposure.

### Vulnerability

- Age structure where legally and ethically appropriate.
- Healthcare access.
- Existing environmental burden.
- Economic resilience.
- Regional ESG resilience.
- Historical sensitivity.

### Severity

- Expected threshold exceedance.
- Expected duration.
- Geographic extent.
- Compound hazards.
- Forecast uncertainty.

## 3.4 Alert lifecycle

Statuses:

```text
DRAFT
PENDING_REVIEW
ACTIVE
ACKNOWLEDGED
RESOLVED
CANCELLED
FALSE_ALARM
MISSED_EVENT
```

Every transition requires:

- Timestamp.
- Actor.
- Reason.
- Model version.
- Data version.
- Previous status.
- New status.

## 3.5 Hysteresis

Avoid alert flapping:

- Activation threshold must differ from deactivation threshold.
- Require minimum persistence period.
- Require consecutive model runs for critical level changes.
- Store alert cooldown.
- Do not create duplicate active alerts for the same event.

## 3.6 Crisis API

Create:

```text
GET  /api/v1/crisis/alerts
GET  /api/v1/crisis/alerts/{id}
GET  /api/v1/crisis/regions/{region_id}/risk
GET  /api/v1/crisis/events/history
POST /api/v1/admin/crisis/evaluate
POST /api/v1/admin/crisis/alerts/{id}/approve
POST /api/v1/admin/crisis/alerts/{id}/reject
POST /api/v1/admin/crisis/alerts/{id}/resolve
```

## Exit criteria

- Air-pollution and heat alerts work end to end.
- Each alert has probability, interval and explanation.
- Alert lifecycle is persisted.
- Duplicate and flapping alerts are prevented.
- Critical alerts require explicit review by default.
- Historical event evaluation is available.

---

# PHASE 4 — Monitoring and MLOps

**Duration:** 2–3 weeks  
**Priority:** High

## 4.1 Prometheus metrics

Add persistent metrics using the project's existing metrics conventions:

```text
sora_environmental_ingestion_total
sora_environmental_ingestion_errors_total
sora_environmental_source_freshness_seconds
sora_environmental_data_quality_score
sora_environmental_observations_total
sora_environmental_forecast_requests_total
sora_environmental_forecast_latency_seconds
sora_environmental_forecast_mae
sora_environmental_forecast_mase
sora_environmental_interval_coverage
sora_crisis_alerts_total
sora_crisis_active_alerts
sora_crisis_false_alarms_total
sora_crisis_missed_events_total
sora_crisis_warning_lead_time_hours
sora_crisis_alert_acknowledgement_seconds
```

Do not make critical metrics dependent on a frontend page being open.

## 4.2 Grafana dashboards

Provision automatically:

### Environmental Data Dashboard

- Source status.
- Freshness.
- Record volume.
- Rejected records.
- Data-quality score.
- Missing values.
- Regional coverage.

### Forecast Performance Dashboard

- MAE, RMSE and MASE over time.
- Metric by region.
- Metric by horizon.
- Champion versus baseline.
- Prediction interval coverage.
- Latency.
- Model version.

### Crisis Warning Dashboard

- Active alerts.
- Alerts by severity.
- Geographic distribution.
- Lead time.
- False-alarm rate.
- Missed-event rate.
- Acknowledgement time.
- Event outcomes.

## 4.3 Promotion gates

A forecast model can be promoted only if:

- Data-quality gate passes.
- Temporal backtest passes.
- It beats baseline by configured minimum.
- Confidence interval supports improvement.
- Interval coverage is acceptable.
- Critical-region performance does not degrade.
- Latency and memory limits pass.
- Model artifact and metadata are stored.
- Rollback artifact exists.

## 4.4 Shadow mode

New models must:

- Receive the same production features.
- Not affect user-visible answers.
- Log predictions and latency.
- Be compared after outcomes become available.
- Be promoted gradually.

## Exit criteria

- Dashboards are auto-provisioned.
- Alerts for stale data and failed ingestion exist.
- Champion/challenger comparison is automated.
- Every production forecast is traceable.
- Rollback is tested.

---

# PHASE 5 — User Interface

**Duration:** 2–4 weeks  
**Priority:** Medium

## Pages

### Environmental Overview

- Current indicators.
- Trend.
- Forecast.
- Uncertainty.
- Data freshness.
- Region selector.

### Crisis Map

- Green, Yellow, Orange and Red regions.
- Active hazards.
- Population under exposure.
- Forecast horizon.
- Confidence.
- Data freshness.

### Alert Details

- What may happen.
- Where.
- When.
- Probability.
- Expected impact.
- Top contributing factors.
- Recommended actions.
- Model and data versions.
- Approval history.

### Model Evidence

- Champion and challenger.
- Baselines.
- Historical performance.
- Region performance.
- Crisis performance.
- Known limitations.

## Requirements

- No alarmist language.
- Uncertainty is visible.
- Stale data are clearly marked.
- User can distinguish observation from forecast.
- User can distinguish model warning from official warning.
- Accessibility and mobile layout are required.
- Russian and English are required initially.

## Exit criteria

- Forecast and crisis map use live API data.
- User sees uncertainty and freshness.
- Critical alert can be reviewed by an authorised user.
- Audit history is visible.

---

# PHASE 6 — Research Validation

**Duration:** 3–5 weeks  
**Priority:** Critical for conference submission

## Historical replay

Select documented historical environmental events and replay them without future information.

For each event record:

- First detection time.
- Warning issue time.
- Event start time.
- Maximum severity.
- Warning lead time.
- Peak prediction error.
- Final outcome.
- Whether it was detected.
- Whether it was a false alarm.

## Human-AI study

Compare:

1. Specialist without SORA.Earth.
2. SORA.Earth alone.
3. Specialist with SORA.Earth.

Measure:

- Accuracy.
- Recall of real crises.
- False-alarm rate.
- Decision time.
- Calibration.
- Confidence.
- Expert acceptance.
- Number of corrected model decisions.

## Ablation study

Measure contribution of:

- Weather features.
- Air-quality history.
- Regional vulnerability.
- Satellite/fire data.
- Event/news signals.
- Ensemble.
- Uncertainty calibration.

## Frozen test set

- Create before final tuning.
- Store dataset hash.
- Do not tune using the frozen set.
- Record model and code commit.
- Produce reproducible evaluation command.

## Exit criteria

- Historical replay is reproducible.
- Frozen test set exists.
- Results include confidence intervals.
- Failed cases and limitations are documented.
- Conference claims are supported by measured evidence.

---

# PHASE 7 — BRICS Expansion

**Duration:** After validated Russian pilot  
**Priority:** Strategic

## Objectives

- Add selected regions from at least two other BRICS countries.
- Support multilingual explanations.
- Compare model transferability.
- Build country-specific vulnerability configurations.
- Add cross-border hazards.
- Document data sovereignty and licensing.
- Support local deployment where required.

## Rules

- Do not assume one model works equally across countries.
- Validate separately by country.
- Keep local thresholds configurable.
- Preserve source licensing metadata.
- Avoid unsupported political-risk conclusions.
- Separate scientific forecast from policy recommendation.

---

# PHASE 8 — Production Hardening

**Duration:** Based on measured load  
**Priority:** Conditional

Implement only when justified by monitoring and load tests:

- Multiple backend replicas.
- PostgreSQL read replicas.
- Managed object storage for model artifacts.
- Redis high availability.
- Queue-based heavy jobs.
- Kubernetes deployment.
- Autoscaling.
- Disaster-recovery testing.
- Backup restoration drills.
- Regional deployment.

Kubernetes is not an MVP requirement.

---

## 6. Testing Strategy

Required test layers:

1. Unit tests.
2. Schema validation tests.
3. Ingester contract tests.
4. API-mocking tests.
5. Database integration tests.
6. Migration tests.
7. Scheduler idempotency tests.
8. Redis-lock tests.
9. Feature leakage tests.
10. Temporal backtesting tests.
11. Alert lifecycle tests.
12. Alert hysteresis tests.
13. Notification tests.
14. End-to-end API tests.
15. Production smoke tests.
16. Frontend type-check and build.
17. Load tests for forecast and map endpoints.

External APIs must be mocked in regular CI. Live API tests must be optional and separately marked.

---

## 7. Security and Governance

- JWT/RBAC for administrative actions.
- Separate roles: viewer, analyst, reviewer and administrator.
- Rate limiting.
- Input validation.
- Audit log.
- Secrets only through environment or secret manager.
- No personal data unless explicitly justified.
- Source licensing registry.
- Model Card for every production model.
- Data Card for every major dataset.
- Emergency disable switch.
- Manual rollback.
- Expert approval for Red alerts.
- Clear disclaimer that SORA.Earth is not an official emergency service.

---

## 8. Success Metrics

### Data

- Source availability at least 99%.
- Data freshness within source-specific SLA.
- Data-quality score at least 90.
- Duplicate rate below 0.5%.
- Full provenance coverage.

### Forecast

- Better than seasonal naive on frozen test.
- 90% interval coverage close to nominal target.
- p95 inference latency below 200 ms initially.
- No critical region with undocumented major degradation.
- Reproducible backtest.

### Crisis warning

- Recall measured on historical crisis events.
- False-alarm rate measured and reported.
- Missed-event rate measured and reported.
- Median warning lead time measured.
- Alert acknowledgement time measured.
- Every alert contains evidence and uncertainty.

### Conference

- Reproducible historical replay.
- Frozen benchmark.
- Ablation study.
- Regional performance.
- Model Card and Data Card.
- Live demonstration.
- Russian and English interface.
- Honest limitations.

---

## 9. Priority Order

### Immediate

1. Repository audit.
2. Fix existing forecasting observability defects.
3. Measure real forecasting baseline.
4. Establish environmental schema.
5. Implement OpenAQ and weather ingestion.

### Short term

6. Data-quality pipeline.
7. PM2.5 and heat baselines.
8. Temporal backtesting.
9. CatBoost challenger.
10. Forecast uncertainty.

### Medium term

11. Crisis taxonomy.
12. Risk engine.
13. Alert lifecycle.
14. Crisis map.
15. Historical replay.

### Long term

16. Fire and drought risks.
17. Human-AI benchmark.
18. Multilingual support.
19. BRICS country expansion.
20. Scalability based on actual traffic.

---

## 10. Definition of Done

A roadmap task is complete only when:

- Production code is implemented.
- Tests pass.
- Migration exists if required.
- API schema is documented.
- Metrics exist.
- Logs are structured.
- Error handling exists.
- Security impact is reviewed.
- Documentation is updated.
- No existing tests regress.
- Changes are committed.
- Production verification is recorded when deployed.

---

## 11. Claude Code Workflow

For every phase Claude Code must:

1. Read this roadmap.
2. Inspect relevant existing files.
3. Present an audit and implementation plan.
4. List files to create or modify.
5. Identify risks and compatibility concerns.
6. Wait for approval before major changes.
7. Implement one vertical slice at a time.
8. Add tests together with code.
9. Run focused tests.
10. Run the complete test suite.
11. Run frontend checks when frontend changes.
12. Show `git diff --stat`.
13. Summarize measured results.
14. Update this roadmap.
15. Propose a commit message.
16. Do not deploy without explicit approval.

---

## 12. Current First Task

Start only with PHASE 0.

Required output:

- `docs/ENVIRONMENTAL_BASELINE_AUDIT.md`
- Actual test results.
- Current database and API map.
- Current forecast-model inventory.
- Current data-source inventory.
- Existing defects.
- Recommended Phase 1 implementation plan.

Do not implement PHASE 1 before PHASE 0 review.
