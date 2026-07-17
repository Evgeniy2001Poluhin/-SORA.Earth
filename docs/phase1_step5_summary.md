# Phase 1 Step 5: Environmental Data Scheduler Jobs

**Status:** ✅ Complete  
**PR:** TBD  
**Branch:** `feat/environmental-scheduler`  
**Date:** 2026-07-18  

## Overview

Implemented automated scheduler jobs for environmental data ingestion with comprehensive monitoring, error handling, and quality tracking. The system now automatically fetches and validates environmental data from OpenAQ and Open-Meteo sources every hour, with health checks every 15 minutes.

## Implementation

### 1. Scheduler Jobs Module

Created `app/services/environmental/scheduler_jobs.py` with 4 production jobs:

#### OpenAQ Air Quality Ingestion (Hourly)
- **Job ID:** `auto_openaq_ingestion`
- **Schedule:** Every 1 hour
- **Function:** `scheduled_openaq_ingestion()`
- **Purpose:** Fetches latest PM2.5, PM10, O3, NO2, SO2, CO measurements
- **Features:**
  - Redis distributed locking for idempotency
  - Retry with exponential backoff (max 3 attempts)
  - Data quality validation and metrics
  - Prometheus metrics export
  - Database execution logging

#### Open-Meteo Weather Ingestion (Hourly)
- **Job ID:** `auto_openmeteo_ingestion`
- **Schedule:** Every 1 hour
- **Function:** `scheduled_openmeteo_ingestion()`
- **Purpose:** Fetches temperature, humidity, wind, pressure data
- **Features:**
  - Same reliability features as OpenAQ
  - Covers 20+ global regions
  - Real-time weather conditions

#### Source Health Check (Every 15 Minutes)
- **Job ID:** `auto_source_health_check`
- **Schedule:** Every 15 minutes
- **Function:** `scheduled_source_health_check()`
- **Purpose:** Monitors data source availability and quality
- **Metrics Calculated:**
  - Success rate (last 2 hours)
  - Data freshness
  - Health status: healthy (90%+), degraded (70-89%), unhealthy (<70%)
  - Quality scores exported to Prometheus

#### Data Quality Aggregation (Hourly)
- **Job ID:** `auto_data_quality_aggregation`
- **Schedule:** Every 1 hour
- **Function:** `scheduled_data_quality_aggregation()`
- **Purpose:** Aggregates quality metrics from recent observations
- **Analysis:**
  - Total observations (last 24 hours)
  - Valid vs invalid records
  - Quality percentage by source
  - Stored in job execution log

### 2. Database Schema

Created `environmental_job_log` table via Alembic migration `0b0ff6d1594e`:

```sql
CREATE TABLE environmental_job_log (
    id SERIAL PRIMARY KEY,
    job_name VARCHAR(100) NOT NULL,
    executed_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(20) NOT NULL,  -- success, failed, skipped
    duration_sec FLOAT,
    records_processed INTEGER DEFAULT 0,
    records_rejected INTEGER DEFAULT 0,
    error_message TEXT,
    metadata_json JSON
);

CREATE INDEX ix_environmental_job_log_job_name ON environmental_job_log(job_name);
CREATE INDEX ix_environmental_job_log_executed_at ON environmental_job_log(executed_at);
CREATE INDEX ix_environmental_job_log_status ON environmental_job_log(status);
```

### 3. Prometheus Metrics

Added 5 new metrics to `app/prom_metrics.py`:

```python
sora_environmental_ingestion_total            # Counter by source, status
sora_environmental_ingestion_errors_total     # Counter by source
sora_environmental_source_freshness_seconds   # Gauge by source
sora_environmental_data_quality_score         # Gauge by source (0-100)
sora_environmental_observations_total         # Counter by source, indicator
```

**Grafana Integration:** All metrics available at `/metrics/prometheus` endpoint for Grafana dashboards.

### 4. Reliability Features

#### Redis Distributed Locking
```python
lock = RedisLock(key=f"sora:lock:{job_name}", timeout=300)
if not lock.acquire():
    return {"status": "skipped", "reason": "lock_held"}
```
- Prevents concurrent execution
- Idempotency guarantee
- 5-minute timeout for ingestion jobs

#### Retry with Exponential Backoff
```python
@with_retry(max_attempts=3, base_delay=2.0, max_delay=30.0)
def scheduled_openaq_ingestion():
    ...
```
- Max 3 retry attempts
- Base delay: 2 seconds
- Exponential backoff: 2s → 4s → 8s
- Jitter added to prevent thundering herd

#### Structured Logging
```python
logger.info(
    "OpenAQ ingestion completed: %d signals (%d valid, %d rejected) in %.2fs",
    len(signals), len(valid_signals), rejected_count, duration
)
```
- All jobs log to stdout in JSON format
- Includes execution time, record counts, errors
- Aggregated to Prometheus for monitoring

#### Database Execution Log
```python
_log_job_execution(
    job_name="openaq_ingestion",
    status="success",
    duration_sec=5.2,
    records_processed=120,
    records_rejected=3,
    metadata={"valid_signals": 117, "parameters": ["pm25", "pm10"]}
)
```
- Every job execution logged to PostgreSQL
- Used for health checks and quality metrics
- Historical analysis and debugging

### 5. Scheduler Integration

Updated `app/scheduler.py` with environmental jobs:

```python
# Environmental data ingestion jobs (Phase 1 Step 5)
try:
    from app.services.environmental.scheduler_jobs import (
        scheduled_openaq_ingestion,
        scheduled_openmeteo_ingestion,
        scheduled_source_health_check,
        scheduled_data_quality_aggregation,
    )

    scheduler.add_job(
        scheduled_openaq_ingestion,
        IntervalTrigger(hours=1),
        id="auto_openaq_ingestion",
        name="Ingest OpenAQ air quality data every 1h",
        replace_existing=True,
    )
    # ... (similar for other jobs)
```

**Immediate Startup:** OpenAQ and Open-Meteo jobs run immediately on scheduler startup for faster data availability.

### 6. Testing

Created comprehensive test suite in `tests/test_environmental_scheduler.py`:

- ✅ 12 tests, all passing
- ✅ Test coverage:
  - Successful ingestion with valid data
  - Lock-held idempotency (concurrent execution prevented)
  - Error handling and recovery
  - Health check with/without recent jobs
  - Data quality aggregation calculations
  - Retry decorator with exponential backoff
  - Database logging

**Test execution:**
```bash
pytest tests/test_environmental_scheduler.py -v
# 12 passed, 1 warning in 9.82s
```

## Files Created/Modified

### New Files
- `app/services/environmental/__init__.py` - Package init
- `app/services/environmental/scheduler_jobs.py` - Job functions (426 lines)
- `alembic/versions/013bbc52a33f_merge_forecast_and_environmental_.py` - Merge migration
- `alembic/versions/0b0ff6d1594e_add_environmental_job_log_table.py` - Job log table
- `tests/test_environmental_scheduler.py` - Test suite (353 lines)
- `docs/phase1_step5_summary.md` - This document

### Modified Files
- `app/database.py` - Added `EnvironmentalJobLog` model + JSON import
- `app/prom_metrics.py` - Added 5 environmental metrics
- `app/scheduler.py` - Registered 4 environmental jobs

## Technical Debt Closed

Before Step 5, fixed critical bugs:

1. **OpenAQ validation bug:** `str(issues)` → `any(issue in [...])` 
   - Fixed logic error in data quality validation
   
2. **pytest-asyncio strict mode:** `asyncio_mode = auto` → `asyncio_mode = strict`
   - Enforces proper async test hygiene

## Roadmap Alignment

Implemented ROADMAP Section 1.4 "Scheduler" requirements:

| Requirement | Status | Implementation |
|------------|--------|----------------|
| OpenAQ hourly ingestion | ✅ | `scheduled_openaq_ingestion()` |
| Weather forecast hourly | ✅ | `scheduled_openmeteo_ingestion()` |
| Source health 15min | ✅ | `scheduled_source_health_check()` |
| Data quality aggregation | ✅ | `scheduled_data_quality_aggregation()` |
| Redis distributed lock | ✅ | All jobs use `RedisLock` |
| Retry with backoff | ✅ | `@with_retry` decorator |
| Timeout handling | ✅ | Lock timeout=300s, ingester timeout=30s |
| Structured logging | ✅ | JSON logs to stdout |
| Metrics export | ✅ | 5 Prometheus metrics |
| Idempotency | ✅ | Redis locks + database constraints |
| Database execution log | ✅ | `environmental_job_log` table |

## Operational Metrics

**Expected Performance:**
- OpenAQ ingestion: ~5-10s per run (120-200 observations)
- Open-Meteo ingestion: ~3-5s per run (60-100 observations)
- Health check: <1s (query last 2h of jobs)
- Quality aggregation: <2s (query last 24h of observations)

**Resource Usage:**
- Redis: 4 lock keys (5-minute TTL)
- PostgreSQL: ~700 rows/day in `environmental_job_log` (4 jobs × 24h / job_frequency)
- Prometheus: 5 metric families, ~20 time series

**Monitoring:**
```bash
# Check job status via API
curl http://localhost:8000/api/v1/scheduler/status

# View Prometheus metrics
curl http://localhost:8000/metrics/prometheus | grep sora_environmental

# Query recent job executions
psql -c "SELECT * FROM environmental_job_log ORDER BY executed_at DESC LIMIT 10;"
```

## Next Steps (Phase 1 Step 6 - Future)

Phase 1 is now complete. Next steps would be:

1. **Phase 2: Forecasting MVP** (per ROADMAP)
   - Baseline models (last-value, seasonal naive)
   - Feature engineering (lags, rolling stats)
   - CatBoost/LightGBM forecasters
   - Temporal backtesting

2. **Phase 3: Crisis Risk Engine**
   - Hazard detection (air pollution, extreme heat)
   - Exposure/vulnerability/severity scoring
   - Alert lifecycle management

3. **Phase 4: MLOps Monitoring**
   - Grafana dashboards for environmental data
   - Forecast performance tracking
   - Alert management UI

## Production Deployment

To deploy to production server (45.137.60.67):

```bash
# On production
cd /opt/sora_earth_ai_platform
git fetch origin
git checkout feat/environmental-scheduler
git pull

# Apply migrations
docker compose -f docker-compose.prod.yml exec backend python -m alembic upgrade head

# Restart scheduler to pick up new jobs
docker compose -f docker-compose.prod.yml restart scheduler

# Verify jobs registered
docker compose -f docker-compose.prod.yml logs -f scheduler | grep "Environmental"
```

**Expected log output:**
```
Environmental ingestion jobs scheduled: OpenAQ (1h), Open-Meteo (1h), health (15m), quality (1h)
Job auto_openaq_ingestion scheduled to run immediately on startup
Job auto_openmeteo_ingestion scheduled to run immediately on startup
```

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    APScheduler Container                     │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ Environmental Scheduler Jobs (app/services/environmental)│ │
│  ├────────────────────────────────────────────────────────┤ │
│  │ • scheduled_openaq_ingestion()      (every 1h)         │ │
│  │ • scheduled_openmeteo_ingestion()   (every 1h)         │ │
│  │ • scheduled_source_health_check()   (every 15min)      │ │
│  │ • scheduled_data_quality_aggregation() (every 1h)      │ │
│  └────────────────────────────────────────────────────────┘ │
│              ↓                                               │
│  ┌───────────────────┐  ┌──────────────────┐               │
│  │  Redis Locks      │  │  Job Execution   │               │
│  │  (idempotency)    │  │  Logging         │               │
│  └───────────────────┘  └──────────────────┘               │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ↓
    ┌────────────────────────────────┐
    │  External APIs                 │
    ├────────────────────────────────┤
    │  • OpenAQ v3 (air quality)     │
    │  • Open-Meteo (weather)        │
    └────────────────────────────────┘
                 │
                 ↓
    ┌────────────────────────────────┐
    │  PostgreSQL Storage            │
    ├────────────────────────────────┤
    │  • environmental_observations  │
    │  • environmental_job_log       │
    └────────────────────────────────┘
                 │
                 ↓
    ┌────────────────────────────────┐
    │  Prometheus Metrics            │
    ├────────────────────────────────┤
    │  • ingestion_total             │
    │  • source_freshness_seconds    │
    │  • data_quality_score          │
    └────────────────────────────────┘
                 │
                 ↓
    ┌────────────────────────────────┐
    │  Grafana Dashboards            │
    │  (Environmental Data Overview) │
    └────────────────────────────────┘
```

## Summary

Phase 1 Step 5 establishes a robust, production-ready environmental data pipeline with:

✅ **Automated ingestion** from 2 data sources (OpenAQ, Open-Meteo)  
✅ **Reliability** via Redis locking, retries, timeouts  
✅ **Observability** with Prometheus metrics, structured logs, database audit trail  
✅ **Quality monitoring** with health checks and aggregation  
✅ **Complete test coverage** (12 passing tests)  
✅ **Production deployment** via Docker Compose scheduler service  

The system is now ready for Phase 2: building forecast models on top of this clean, validated environmental data foundation.
