"""SORA.Earth — domain-level Prometheus metrics.

Import from anywhere:  from app.prom_metrics import sora_retrain_total, ...
All metrics auto-appear on /metrics.

**Every Gauge here declares a `multiprocess_mode`, and that is not optional.**
The backend runs as `gunicorn -w 4`, so each metric lives in one of four
worker processes. Measured on production 2026-09-06: five scrapes of
`/metrics` landed on two different workers, one reporting two
`sora_telemetry_tasks_total` series and the other reporting none, for an event
that had definitely happened. Prometheus scrapes the same single address, so it
saw an arbitrary worker (#262).

With `PROMETHEUS_MULTIPROC_DIR` set, counters and histograms are summed across
processes automatically. Gauges are not: without a declared mode
`prometheus_client` emits one series **per process id**, which is worse than
the problem -- four series where an alert expects one. Each gauge below is
therefore given a mode on purpose, and the reason is written next to it.

Two kinds do not survive the switch at all, so neither is used here:
`Info` (constructed and set without error, then absent from the aggregated
scrape -- measured) and `Gauge.set_function` (accepted, never collected --
measured). Both are the shape this repository keeps finding: something that
looks like a measurement and reports nothing.
"""
from prometheus_client import Counter, Histogram, Gauge

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
#: `mostrecent`: one number about the whole system, recomputed at scrape time
#: by whichever worker serves it. Summing four workers' answers would be
#: meaningless, and `max` would report the staleness of the worker that
#: happened to look longest ago.
sora_retrain_seconds_since_success = Gauge(
    "sora_retrain_seconds_since_success",
    "Seconds since the last successful retrain; -1 if there has never been one",
    multiprocess_mode="mostrecent",
)

# ── Predictions ──
sora_prediction_latency = Histogram(
    "sora_prediction_latency_ms", "Prediction latency in ms",
    buckets=[5,250, 500, 1000, 2500],
)
sora_predictions_total  = Counter("sora_predictions_total",   "Predictions served", ["model"])

#: MLflow telemetry dispatched off the request path (#255).
#:
#: Four outcomes and all four reachable: `scheduled` on submission,
#: `succeeded` / `failed` when the task finishes, and `dropped` when the
#: in-flight bound turns a submission away -- which a 500-sample monte-carlo
#: against a slow tracking server reaches. `dropped` is the one worth alerting
#: on: it means observability is being shed, and shedding it silently is how
#: an outage looks like calm.
#: Labelled by operation as well, because the two kinds of drop are not the
#: same event. Measured 2026-09-06: one `POST /evaluate/monte-carlo` submits
#: 500 `log_evaluation` tasks in about a millisecond -- faster than any worker
#: can drain them -- so 94% are shed whatever MLflow's latency is. Without the
#: label that noise buries `dropped{operation="log_prediction"}`, which is the
#: one that means observability is actually being lost. The per-sample logging
#: itself is #258; the label is what makes the counter readable until then.
sora_telemetry_tasks_total = Counter(
    "sora_telemetry_tasks_total", "MLflow telemetry tasks by outcome",
    ["outcome", "operation"])

# ── Model quality (set after retrain / on startup) ──
#
# `mostrecent`: the quality of the one model being served. Nothing in `app/`
# sets either of these today -- grep found the definitions and no writer -- so
# they publish nothing at all. That is a separate question from this one and is
# left as it is; the mode is declared so that whoever starts setting them gets
# one series rather than four.
sora_model_auc          = Gauge("sora_model_auc",     "Current model AUC-ROC",
                                multiprocess_mode="mostrecent")
sora_model_accuracy     = Gauge("sora_model_accuracy", "Current model accuracy",
                                multiprocess_mode="mostrecent")

# ── Forecast model performance (updated after walk-forward validation) ──
# `mostrecent` throughout: each is the latest walk-forward result for one
# (metric, model) pair, not work done by a process. These four are set in
# `app/scheduler.py`, which runs in the **scheduler container** -- a separate
# process that serves no HTTP and is scraped by nothing, so they do not reach
# Prometheus from there today either way. Filed separately; the mode is right
# regardless of which process ends up writing them.
sora_forecast_mae       = Gauge("sora_forecast_mae_current",  "Current forecast MAE",  ["metric", "model"],
                                multiprocess_mode="mostrecent")
sora_forecast_rmse      = Gauge("sora_forecast_rmse_current", "Current forecast RMSE", ["metric", "model"],
                                multiprocess_mode="mostrecent")
sora_forecast_r2        = Gauge("sora_forecast_r2_current",   "Current forecast R²",   ["metric", "model"],
                                multiprocess_mode="mostrecent")
sora_forecast_mape      = Gauge("sora_forecast_mape_current", "Current forecast MAPE (%)", ["metric", "model"],
                                multiprocess_mode="mostrecent")

# ── Forecast LSTM status (updated via scheduler refresh) ──
# `mostrecent`: state of one shared model, computed in whichever worker served
# the request that refreshed it (`app/api/forecast.py`). Summing would multiply
# a sample count by the number of workers that happened to look.
sora_forecast_samples_total   = Gauge("sora_forecast_samples_total",   "Total evaluation samples available for LSTM",
                                      multiprocess_mode="mostrecent")
sora_forecast_lstm_active     = Gauge("sora_forecast_lstm_active",     "LSTM model active status (1=active, 0=inactive)",
                                      multiprocess_mode="mostrecent")
sora_forecast_days_remaining  = Gauge("sora_forecast_days_remaining",  "Days until LSTM activation threshold",
                                      multiprocess_mode="mostrecent")

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
#: `mostrecent`: freshness of a source, per source. `max` would look tempting
#: -- "the worst staleness" -- but the label already separates the sources, and
#: across processes the answers are the same question asked at different
#: moments, not different things to compare.
sora_environmental_source_freshness_seconds = Gauge(
    "sora_environmental_source_freshness_seconds",
    "Time since most recent observation (seconds)",
    ["source"],
    multiprocess_mode="mostrecent",
)
sora_environmental_data_quality_score = Gauge(
    "sora_environmental_data_quality_score",
    "Data quality score (0-100)",
    ["source"],
    multiprocess_mode="mostrecent"
)
sora_environmental_observations_total = Counter(
    "sora_environmental_observations_total",
    "Total environmental observations ingested",
    ["source", "indicator"]
)

# ── App info ──
#
# A labelled Gauge rather than `Info`, because `Info` does not survive
# multiprocess mode: it constructs and sets without error and is then simply
# absent from the aggregated scrape -- measured, not read in a changelog. The
# series is byte-identical either way, asserted in
# `tests/test_prometheus_multiprocess.py`:
#     sora_app_info{platform="SORA.Earth",version="2.0.0"} 1.0
sora_app_info = Gauge(
    "sora_app_info", "Application metadata", ["version", "platform"],
    multiprocess_mode="mostrecent",
)
sora_app_info.labels(version="2.0.0", platform="SORA.Earth").set(1)
