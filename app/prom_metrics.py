"""SORA.Earth — domain-level Prometheus metrics.

Import from anywhere:  from app.prom_metrics import sora_retrain_total, ...
All metrics auto-appear on /metrics via shared default registry.
"""
from prometheus_client import Counter, Histogram, Gauge, Info

# ── MLOps lifecycle ──
sora_retrain_total      = Counter("sora_retrain_total",       "Retrain runs",          ["status"])
sora_refresh_total      = Counter("sora_refresh_total",       "Data refresh runs",     ["status"])
sora_full_pipeline_total= Counter("sora_full_pipeline_total", "Full pipeline runs",    ["status"])
sora_drift_detected     = Counter("sora_drift_detected_total","Drift detection events")
sora_model_promoted     = Counter("sora_model_promoted_total","Models promoted")
sora_model_rejected     = Counter("sora_model_rejected_total","Models rejected")

# ── Predictions ──
sora_prediction_latency = Histogram(
    "sora_prediction_latency_ms", "Prediction latency in ms",
    buckets=[5,250, 500, 1000, 2500],
)
sora_predictions_total  = Counter("sora_predictions_total",   "Predictions served", ["model"])

# ── Model quality (set after retrain / on startup) ──
sora_model_auc          = Gauge("sora_model_auc",     "Current model AUC-ROC")
sora_model_accuracy     = Gauge("sora_model_accuracy", "Current model accuracy")

# ── Forecast model performance (updated after walk-forward validation) ──
sora_forecast_mae       = Gauge("sora_forecast_mae_current",  "Current forecast MAE",  ["metric", "model"])
sora_forecast_rmse      = Gauge("sora_forecast_rmse_current", "Current forecast RMSE", ["metric", "model"])
sora_forecast_r2        = Gauge("sora_forecast_r2_current",   "Current forecast R²",   ["metric", "model"])
sora_forecast_mape      = Gauge("sora_forecast_mape_current", "Current forecast MAPE (%)", ["metric", "model"])

# ── Forecast LSTM status (updated via scheduler refresh) ──
sora_forecast_samples_total   = Gauge("sora_forecast_samples_total",   "Total evaluation samples available for LSTM")
sora_forecast_lstm_active     = Gauge("sora_forecast_lstm_active",     "LSTM model active status (1=active, 0=inactive)")
sora_forecast_days_remaining  = Gauge("sora_forecast_days_remaining",  "Days until LSTM activation threshold")

# ── App info ──
sora_app_info           = Info("sora_app", "Application metadata")
sora_app_info.info({"version": "2.0.0", "platform": "SORA.Earth"})
