# Deployment Instructions - Task 3 (Model Performance Monitoring)

## Current Status on Production

**Server:** 45.137.60.67  
**Docker Image:** 2 days old (does NOT include Task 3 changes)  
**Code Files:** Updated (includes new endpoints)  
**Database Migration:** Not yet applied

## Files Changed in Task 3

### New Files
1. `tests/test_forecast_metrics.py` - Test suite
2. `alembic/versions/a8d9becb25de_add_forecast_model_metrics_table.py` - Database migration
3. `.claude/TASK3_COMPLETION_REPORT.md` - Documentation

### Modified Files
1. `app/database.py` - Added `ForecastModelMetrics` model
2. `app/scheduler.py` - Enhanced `scheduled_pretrain_forecast_models()`
3. `app/api/forecast.py` - Added 2 new API endpoints:
   - `GET /api/v1/forecast/metrics/performance`
   - `GET /api/v1/forecast/metrics/latest`

## Deployment Steps

### 1. Connect to Production Server

```bash
ssh root@45.137.60.67
cd /opt/sora_earth_ai_platform
```

### 2. Pull Latest Code

```bash
git pull origin main
```

### 3. Apply Database Migration

```bash
# Via Docker backend container
docker compose -f docker-compose.prod.yml exec backend \
  alembic upgrade head

# Verify table created
docker compose -f docker-compose.prod.yml exec postgres \
  psql -U sora -d sora_earth -c "\d forecast_model_metrics"
```

Expected output:
```
Table "public.forecast_model_metrics"
Column             | Type           | Nullable | Default
-------------------+----------------+----------+---------
id                 | integer        | not null | nextval(...)
trained_at         | timestamp      | not null | 
metric_name        | varchar(50)    | not null | 
model_type         | varchar(50)    | not null | 
train_samples      | integer        | not null | 
test_samples       | integer        |          | 
mae                | double         |          | 
rmse               | double         |          | 
mape               | double         |          | 
r2_score           | double         |          | 
training_duration_sec | double      |          | 
trigger_source     | varchar(50)    |          | 
status             | varchar(50)    | not null | 
error_message      | text           |          | 
metadata_json      | text           |          | 
Indexes:
    "forecast_model_metrics_pkey" PRIMARY KEY, btree (id)
    "ix_forecast_model_metrics_id" btree (id)
    "ix_forecast_model_metrics_trained_at" btree (trained_at)
    "ix_forecast_model_metrics_metric_name" btree (metric_name)
    "ix_forecast_model_metrics_model_type" btree (model_type)
    "ix_forecast_model_metrics_trigger_source" btree (trigger_source)
    "ix_forecast_model_metrics_status" btree (status)
```

### 4. Rebuild and Restart Docker Containers

```bash
# Rebuild backend image with new code
docker compose -f docker-compose.prod.yml build backend

# Restart backend (API server)
docker compose -f docker-compose.prod.yml up -d backend

# Restart scheduler (metrics logging)
docker compose -f docker-compose.prod.yml restart scheduler
```

### 5. Verify Deployment

#### A. Check API Endpoints

```bash
# Test new endpoints
curl https://sora-earth.online/api/v1/forecast/metrics/latest
curl https://sora-earth.online/api/v1/forecast/metrics/performance?limit=10

# Should return JSON (may be empty initially)
```

Expected responses:
- `/metrics/latest` → `{}` (empty until first retraining cycle)
- `/metrics/performance` → `{"total": 0, "metrics": []}`

#### B. Check Scheduler Logs

```bash
# View scheduler logs
docker compose -f docker-compose.prod.yml logs -f scheduler | grep "Forecast pretrain"

# Wait for next 6h cycle or manually trigger
docker compose -f docker-compose.prod.yml exec scheduler \
  python3 -c "from app.scheduler import scheduled_pretrain_forecast_models; print(scheduled_pretrain_forecast_models())"
```

Expected log output:
```
[INFO] Forecast pretrain: ensemble/score fitted (28 rows) - MAE=2.45, RMSE=3.12, R²=0.92
[INFO] Forecast pretrain: ensemble/prob fitted (28 rows) - MAE=0.05, RMSE=0.08, R²=0.95
[INFO] Forecast pretrain: ensemble/co2_reduction fitted (28 rows) - MAE=45.2, RMSE=62.1, R²=0.87
[INFO] Forecast pretrain complete: {'status': 'ok', 'metrics_trained': ['score', 'prob', 'co2_reduction']}
```

#### C. Verify Database Logging

```bash
# Check that metrics are logged to database
docker compose -f docker-compose.prod.yml exec postgres \
  psql -U sora -d sora_earth -c \
  "SELECT COUNT(*) FROM forecast_model_metrics;"

# View latest metrics
docker compose -f docker-compose.prod.yml exec postgres \
  psql -U sora -d sora_earth -c \
  "SELECT trained_at, metric_name, model_type, mae, rmse, r2_score, status 
   FROM forecast_model_metrics 
   ORDER BY trained_at DESC 
   LIMIT 5;"
```

Expected: After first 6h cycle, should see 3 rows (score, prob, co2_reduction).

### 6. Monitor for Issues

```bash
# Monitor backend logs for errors
docker compose -f docker-compose.prod.yml logs -f backend | grep -E "ERROR|WARN"

# Monitor scheduler logs
docker compose -f docker-compose.prod.yml logs -f scheduler | grep -E "ERROR|WARN|Forecast"

# Check container health
docker compose -f docker-compose.prod.yml ps
```

## Rollback Plan

If issues occur, rollback:

```bash
cd /opt/sora_earth_ai_platform

# 1. Revert database migration
docker compose -f docker-compose.prod.yml exec backend \
  alembic downgrade -1

# 2. Checkout previous commit
git log --oneline -5  # Find commit before Task 3
git checkout <previous-commit-hash>

# 3. Rebuild and restart
docker compose -f docker-compose.prod.yml build backend
docker compose -f docker-compose.prod.yml up -d backend
docker compose -f docker-compose.prod.yml restart scheduler
```

## Expected Behavior After Deployment

### Immediate (right after deployment)
- ✅ New API endpoints accessible (but return empty data)
- ✅ Database table `forecast_model_metrics` exists
- ✅ No errors in logs
- ✅ Existing functionality unchanged

### After First 6h Retraining Cycle
- ✅ Metrics logged to database (3 rows: score, prob, co2_reduction)
- ✅ API endpoints return data
- ✅ Scheduler logs show MAE/RMSE/R² values
- ✅ Walk-forward validation completes successfully

### Ongoing
- Every 6h: New metrics logged automatically
- Historical trend data accumulates
- Performance degradation detectable via API queries

## Troubleshooting

### Problem: API endpoints return 404

**Cause:** Docker image not rebuilt  
**Fix:**
```bash
docker compose -f docker-compose.prod.yml build backend
docker compose -f docker-compose.prod.yml up -d backend
```

### Problem: Migration fails with "table already exists"

**Cause:** Table was manually created  
**Fix:**
```bash
# Mark migration as applied without running it
docker compose -f docker-compose.prod.yml exec backend \
  alembic stamp a8d9becb25de
```

### Problem: No metrics logged after 6h

**Check scheduler is running:**
```bash
docker compose -f docker-compose.prod.yml logs scheduler | tail -20
```

**Manually trigger retraining:**
```bash
docker compose -f docker-compose.prod.yml exec scheduler \
  python3 -c "from app.scheduler import scheduled_pretrain_forecast_models; print(scheduled_pretrain_forecast_models())"
```

### Problem: Walk-forward validation is slow

**Expected:** Validation can take 2-3x normal training time  
**Normal:** 15-45 seconds for all 3 metrics  
**Acceptable:** Up to 2 minutes if large dataset

## Post-Deployment Checklist

- [ ] Migration applied successfully
- [ ] Docker image rebuilt and restarted
- [ ] API endpoints accessible (return 200 OK)
- [ ] Database table exists with correct schema
- [ ] Scheduler running without errors
- [ ] First 6h cycle completes successfully
- [ ] Metrics appear in database
- [ ] API endpoints return data
- [ ] No errors in production logs

## Performance Impact

**Database:** +1 table, minimal storage (<1MB for 1000 retraining cycles)  
**API:** +2 endpoints, no impact on existing endpoints  
**Scheduler:** +10-30 seconds per 6h cycle (walk-forward validation)  
**Memory:** No significant change (<50MB overhead)

## Security Notes

- New endpoints are read-only (GET requests only)
- No authentication required (metrics are public monitoring data)
- No PII or sensitive data logged
- SQL injection safe (uses SQLAlchemy ORM)

---

**Deployment Date:** TBD  
**Deployed By:** TBD  
**Rollback Tested:** No (low risk, database migration is reversible)  
**Production Impact:** Zero downtime, backward compatible
