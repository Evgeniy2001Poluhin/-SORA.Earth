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

#: Seconds since the last retrain that finished successfully (#188).
#:
#: The counters above answer "how many runs failed". They cannot answer "has
#: anything run at all", and that is the question that was open for a month:
#: `models/` became root-owned on 17 July, every retrain died on writing its
#: artefact, and because drift never fired the scheduler never even attempted
#: one. No failures were counted, and no failures looked like health.
#:
#: A gauge of age, not a counter of events. An alert on this fires when the
#: system stops trying, which is the state a counter cannot express.
#:
#: -1 means no successful run has ever been recorded. Not 0, which would read
#: as "one just finished", and not absent, which would leave the alert with
#: nothing to compare.
sora_retrain_seconds_since_success = Gauge(
    "sora_retrain_seconds_since_success",
    "Seconds since the last successful retrain; -1 if there has never been one",
)

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

# ── Environmental data ingestion ──
sora_environmental_ingestion_total = Counter(
    "sora_environmental_ingestion_total",
    "Environmental data ingestion runs",
    ["source", "status"]
)
sora_environmental_ingestion_errors_total = Counter(
    "sora_environmental_ingestion_errors_total",
    "Environmental data ingestion errors",
    ["source"]
)
sora_environmental_source_freshness_seconds = Gauge(
    "sora_environmental_source_freshness_seconds",
    "Time since most recent observation (seconds)",
    ["source"]
)
sora_environmental_data_quality_score = Gauge(
    "sora_environmental_data_quality_score",
    "Data quality score (0-100)",
    ["source"]
)
sora_environmental_observations_total = Counter(
    "sora_environmental_observations_total",
    "Total environmental observations ingested",
    ["source", "indicator"]
)

# ── App info ──
sora_app_info           = Info("sora_app", "Application metadata")
sora_app_info.info({"version": "2.0.0", "platform": "SORA.Earth"})
