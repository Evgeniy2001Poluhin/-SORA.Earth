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

# ── App info ──
sora_app_info           = Info("sora_app", "Application metadata")
sora_app_info.info({"version": "2.0.0", "platform": "SORA.Earth"})
