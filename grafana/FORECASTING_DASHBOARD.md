# SORA Forecasting Dashboard Setup

## Overview

This dashboard monitors the progress and performance of LSTM/Prophet forecast models in the SORA MLOps pipeline.

## Metrics Tracked

1. **LSTM Activation Status** (green = active, orange = waiting for data)
2. **Sample Count Progress** (gauge showing 0→60 samples)
3. **Days Until LSTM Activation** (countdown)
4. **Model Weights Over Time** (LSTM vs Prophet weights in ensemble)
5. **Sample Count Growth** (time series of training data accumulation)
6. **Forecast Request Rate** (requests/sec)
7. **Model Comparison (MAE)** (Prophet vs LSTM vs Ensemble accuracy)

## Installation

### Option 1: Import via Grafana UI

1. Open Grafana: `http://localhost:3000` (or production URL)
2. Login: `admin` / `sora2026`
3. Navigate to **Dashboards** → **Import**
4. Upload `forecasting_dashboard.json`
5. Select Prometheus datasource
6. Click **Import**

### Option 2: Automated Provisioning (Recommended for Production)

Add to `docker-compose.yml`:

```yaml
services:
  grafana:
    volumes:
      - ./grafana/dashboards:/etc/grafana/provisioning/dashboards:ro
      - ./grafana/datasources.yml:/etc/grafana/provisioning/datasources/datasources.yml:ro
```

Create `grafana/datasources.yml`:

```yaml
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
```

Restart Grafana:
```bash
docker-compose restart grafana
```

Dashboard will auto-load on startup.

## Metrics Export

The LSTM status endpoint automatically exports metrics to Prometheus when called:

```bash
curl http://localhost:8000/api/v1/forecasting/lstm-status
```

This updates the following gauges:
- `forecast_lstm_active` (0 or 1)
- `forecast_sample_count` (0-60+)
- `forecast_lstm_weight` (0.0-1.0)
- `forecast_prophet_weight` (0.0-1.0)
- `forecast_days_until_lstm` (days remaining)

## Scheduled Updates

To keep metrics fresh, add a cron job or scheduler task:

```python
# In app/scheduler.py
@scheduler.scheduled_job("interval", minutes=5)
def refresh_forecast_metrics():
    """Update Prometheus metrics for Grafana dashboard."""
    from app.api.forecast import get_lstm_status
    from app.main import get_db_sync
    
    db = get_db_sync()
    try:
        asyncio.run(get_lstm_status(db=db))
        log.info("Forecast metrics refreshed for Prometheus")
    finally:
        db.close()
```

## Usage

- **Auto-refresh**: Dashboard refreshes every 10s (configurable)
- **Time range**: Default 1 hour (adjustable with time picker)
- **Annotations**: LSTM activation events are highlighted on charts
- **Alerts**: Configure alerts in Grafana for:
  - `forecast_days_until_lstm < 7` → "LSTM activation approaching"
  - `forecast_lstm_active == 0 AND forecast_sample_count >= 60` → "LSTM should activate but hasn't"

## Troubleshooting

### No Data Showing

1. Check Prometheus is scraping FastAPI:
   ```bash
   curl http://localhost:8000/api/v1/metrics/prometheus | grep forecast
   ```

2. Verify Prometheus config includes FastAPI target:
   ```yaml
   scrape_configs:
     - job_name: 'fastapi'
       static_configs:
         - targets: ['app:8000']
   ```

3. Call LSTM status to populate metrics:
   ```bash
   curl http://localhost:8000/api/v1/forecasting/lstm-status
   ```

### Metrics Not Updating

- Frontend auto-refreshes LSTM widget every 30s, triggering metric export
- Add explicit scheduler job (see above) for guaranteed 5min updates

## Next Steps

1. **Week 1**: Monitor Prophet MAE improvement (target: 4.04 → 3.5)
2. **Week 2-3**: Track LSTM activation progress
3. **Week 4**: Set up A/B testing alerts when LSTM activates

---

**Created**: 2026-07-13  
**Last Updated**: 2026-07-13  
**Owner**: SORA MLOps Team
