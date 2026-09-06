import contextlib
import logging
import os
import re
import threading
import mlflow
import mlflow.sklearn
from datetime import datetime

logger = logging.getLogger(__name__)

MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5556")
#: A new experiment, not `sora-earth-esg` and not `esg` (#189).
#:
#: `sora-earth-esg` (id 1) and `Default` (id 0) were created before the server
#: ran with `--serve-artifacts`, so their `artifact_location` is an absolute
#: path inside the mlflow container -- `/mlflow/artifacts/1`. The client is
#: handed that path and tries to write it locally, which is the
#: `PermissionError: '/mlflow'` that had every registration failing silently
#: since 17 July while retrains reported success.
#:
#: `artifact_location` cannot be changed after creation, so those two are left
#: as they are: they hold real history and rewriting it is not something this
#: repository does.
#:
#: `esg` (id 2) does carry `mlflow-artifacts:/esg` and would work. It is not
#: reused because the name says nothing about which line of runs it was made
#: for, and pointing production at an experiment of unknown purpose trades one
#: unknown for another.
EXPERIMENT_NAME = os.getenv("MLFLOW_EXPERIMENT_NAME", "sora-earth-esg-v2")

#: Proxied through the tracking server, which is what `--serve-artifacts`
#: exists for. Set explicitly rather than left to the server's default: the
#: default is what produced the two broken experiments, and a value that
#: matters should not depend on how the server happened to be started.
EXPERIMENT_ARTIFACT_LOCATION = os.getenv(
    "MLFLOW_ARTIFACT_LOCATION", f"mlflow-artifacts:/{EXPERIMENT_NAME}")

_OFFLINE = os.getenv("SORA_OFFLINE","0")=="1"  # _SORA_OFFLINE_GUARD


def _ensure_experiment(api=None) -> None:
    """Point at the experiment, creating it with a proxied artifact location.

    `set_experiment` alone would create it with whatever the server defaults
    to, and that default is exactly what left experiments 0 and 1 unusable.

    `api` is the injection seam described on `_registry_api`. It matters here
    because this makes network calls: a caller that was handed a substitute
    MLflow must not have one reached around it, or a test proving a path is
    offline would be proving it about a different object than the one the
    path uses.
    """
    api = mlflow if api is None else api
    existing = api.get_experiment_by_name(EXPERIMENT_NAME)
    if existing is None:
        api.create_experiment(
            EXPERIMENT_NAME, artifact_location=EXPERIMENT_ARTIFACT_LOCATION)
    api.set_experiment(EXPERIMENT_NAME)


#: Set once the experiment has been resolved, so the network call below happens
#: at most once per process.
_experiment_ready = False

#: Guards both the flag and the environment window in `_ensure_experiment_once`.
#: FastAPI runs sync handlers in a threadpool, so "first use" can be several
#: threads at once.
_experiment_lock = threading.Lock()

#: How long the *experiment lookup* may take, and how hard it may retry.
#:
#: Measured 2026-09-06 against a port nothing serves: one
#: `get_experiment_by_name` on MLflow's defaults took **247 seconds** -- the
#: documented 120s `MLFLOW_HTTP_REQUEST_TIMEOUT` applies per attempt, and the
#: retry policy multiplies it. That is the wait that used to sit at import.
#:
#: Making the init lazy without this would move the same 247 seconds into the
#: first prediction, which is worse than where it was: a slow startup is
#: visible to whoever deployed, a slow request is not. So the metadata lookup
#: is bounded here -- and only here. The global default is deliberately left
#: alone, because the same setting covers model-artifact uploads during
#: retraining, where a few seconds would break a legitimately long operation.
_EXPERIMENT_LOOKUP_ENV = {
    "MLFLOW_HTTP_REQUEST_TIMEOUT": "5",
    "MLFLOW_HTTP_REQUEST_MAX_RETRIES": "1",
    "MLFLOW_HTTP_REQUEST_BACKOFF_FACTOR": "0",
}


@contextlib.contextmanager
def _bounded_lookup():
    """Apply `_EXPERIMENT_LOOKUP_ENV` for the length of one lookup.

    MLflow reads these per request, so this is the only lever available -- the
    client takes no timeout argument. It is process-global while it is set,
    which is the cost: another thread uploading an artefact during this window
    would get the short timeout too. The window is held under
    `_experiment_lock`, happens at most once per process, and is bounded by the
    five seconds it installs. Stated rather than hidden, because it is a real
    if narrow hazard.
    """
    saved = {k: os.environ.get(k) for k in _EXPERIMENT_LOOKUP_ENV}
    os.environ.update(_EXPERIMENT_LOOKUP_ENV)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _ensure_experiment_once(api=None) -> None:
    """Resolve the experiment on first use, never at import.

    This used to run at module level, and `_ensure_experiment` makes an HTTP
    call. **Importing this module therefore made a network request**, and on
    MLflow's defaults that request costs 247 seconds against an unreachable
    server (measured; see `_EXPERIMENT_LOOKUP_ENV`). So with a tracking server
    configured but not answering, `import app.mlflow_tracking` blocked -- and
    so did importing anything that imports it.

    Nine modules import it at module level, measured by AST and asserted in
    `tests/test_mlflow_import_makes_no_request.py`:
    `app/main.py`, `app/drift_detection.py`, `app/registry_retry.py`,
    `app/api/admin_snapshot.py`, `app/api/drift.py`, `app/api/evaluate.py`,
    `app/api/infra.py`, `app/api/predict.py`, `app/api/retrain.py`.
    None defers the import into a function, so `app.main` -- application
    startup itself -- blocked directly rather than transitively.

    The `try` around it caught exceptions and could not catch slowness, which
    is why it looked safe. Measured 2026-09-05: `import mlflow` 1.1s, `import
    app.mlflow_tracking` over 30s and still going; with `SORA_OFFLINE=1`, 1.0s
    -- which is why CI never paid the cost and nobody saw it (issue 243).

    A failure here is logged and not raised: callers already treat MLflow as
    best-effort, and a tracking server being down must not fail a prediction.
    The flag stays false in that case, so a later call retries.

    `api` forwards the `_registry_api` injection seam. Without it this reached
    around a substituted MLflow to the module global and opened a socket --
    caught by `tests/test_mlflow_registry_isolation.py`, whose whole subject is
    that the registration failure path is provable with no network at all.
    """
    global _experiment_ready
    if _experiment_ready or _OFFLINE:
        return
    with _experiment_lock:
        if _experiment_ready:  # resolved while this thread waited for the lock
            return
        try:
            with _bounded_lookup():
                _ensure_experiment(api)
            _experiment_ready = True
        except Exception as exc:
            logger.warning("MLflow experiment not resolved: %s", _sanitized(exc))


try:
    # Local: this only records a URI on the client. No request is made.
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
except Exception as e:
    logger.warning("MLflow init failed: %s", e)


_MAX_DETAIL = 200
# userinfo in a URI, a query string, and anything that looks like a credential.
_REDACT = (
    re.compile(r"//[^/@\s]*@"),
    re.compile(r"\?[^\s]*"),
    # = and : both appear in the wild, and JSON-ish payloads wrap either side
    # in quotes: api_key=x, api_key: x, "api_key": "x". A quoted value is taken
    # whole -- stopping at whitespace would leave the tail of
    # api_key: "super secret" in the log.
    re.compile(
        r"""(?ix)(token|password|secret|api[_-]?key)["']?\s*[:=]\s*(
            "(?:\\.|[^"\\])*" | '(?:\\.|[^'\\])*' | [^\s&]+
        )"""
    ),
)


def _sanitized(exc: Exception) -> str:
    """A short, redacted description of a failure, safe to put in a log line.

    MLflow errors can carry the tracking URI with its query string, and a URI can
    carry userinfo, so the message is redacted rather than logged verbatim.
    Control characters are neutralised so a failure cannot forge log lines.
    """
    try:
        text = str(exc)
    except Exception:
        # An exception whose __str__ raises must not take the caller down with it.
        return "<unprintable>"
    for pattern in _REDACT:
        text = pattern.sub("[redacted]", text)
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    text = " ".join(text.split())
    if len(text) > _MAX_DETAIL:
        text = text[:_MAX_DETAIL] + "..."
    return text


def _log_mlflow_failure(operation: str, exc: Exception) -> None:
    """MLflow is optional telemetry: report the failure, never raise from it.

    Without this the caller cannot tell an outage from a quiet success -- for
    get_experiment_stats in particular, a failure and an empty experiment both
    produced total_runs = 0.
    """
    logger.warning(
        "MLflow %s failed: %s: %s", operation, type(exc).__name__, _sanitized(exc)
    )


def _to_dict(input_data):
    if input_data is None:
        return {}
    if isinstance(input_data, dict):
        return input_data
    if hasattr(input_data, "model_dump"):
        return input_data.model_dump()
    if hasattr(input_data, "dict"):
        return input_data.dict()
    return {}


def _extract_probability(payload):
    if isinstance(payload, dict):
        for key in ("probability", "success_probability"):
            if payload.get(key) is not None:
                return payload.get(key)
    return None


def _extract_prediction(payload):
    if isinstance(payload, dict):
        return payload.get("prediction")
    return payload


def log_prediction(
    model_name: str,
    input_data,
    prediction=None,
    probability: float = None,
    probability_v2: float = None,
    latency_ms: float = None,
    confidence=None,
    esg_total_score: float = None,
):
    if _OFFLINE:
        return None
    _ensure_experiment_once()
    try:
        params = _to_dict(input_data)

        if isinstance(prediction, dict):
            payload = prediction
            pred_value = _extract_prediction(payload)
            prob_value = probability if probability is not None else _extract_probability(payload)
            prob_v2_value = probability_v2 if probability_v2 is not None else payload.get("probability_v2")
            conf_value = confidence if confidence is not None else payload.get("confidence")
            esg_value = esg_total_score if esg_total_score is not None else payload.get("total_score") or payload.get("esg_total_score")
            latency_value = latency_ms if latency_ms is not None else payload.get("latency_ms")
        else:
            pred_value = prediction
            prob_value = probability
            prob_v2_value = probability_v2
            conf_value = confidence
            esg_value = esg_total_score
            latency_value = latency_ms

        with mlflow.start_run(
            run_name=f"predict_{model_name}_{datetime.now().strftime('%H%M%S')}"
        ):
            if params:
                mlflow.log_params({k: str(v)[:250] for k, v in params.items()})

            metrics = {}
            if pred_value is not None:
                metrics["prediction"] = float(pred_value)
            if prob_value is not None:
                metrics["probability"] = float(prob_value)
            if prob_v2_value is not None:
                metrics["probability_v2"] = float(prob_v2_value)
                if prob_value is not None:
                    metrics["ab_divergence"] = abs(float(prob_value) - float(prob_v2_value))
            if latency_value is not None:
                metrics["latency_ms"] = float(latency_value)
            if esg_value is not None:
                metrics["esg_total_score"] = float(esg_value)

            if metrics:
                mlflow.log_metrics(metrics)

            mlflow.set_tag("model", model_name)
            mlflow.set_tag("type", "prediction")
            if conf_value is not None:
                mlflow.set_tag("confidence", str(conf_value))
    except Exception as e:
        _log_mlflow_failure("log_prediction", e)


def log_evaluation(project_name: str, esg_scores: dict, risk_level: str):
    if _OFFLINE:
        return None
    _ensure_experiment_once()
    try:
        with mlflow.start_run(run_name=f"eval_{project_name}_{datetime.now().strftime('%H%M%S')}"):
            metrics = {
                "total_score": esg_scores.get("total_score", 0),
                "environment_score": esg_scores.get("environment_score", 0),
                "social_score": esg_scores.get("social_score", 0),
                "economic_score": esg_scores.get("economic_score", 0),
                "success_probability": esg_scores.get("success_probability", 0),
            }
            if esg_scores.get("success_probability_v2") is not None:
                metrics["success_probability_v2"] = esg_scores["success_probability_v2"]
                metrics["ab_divergence"] = abs(metrics["success_probability"] - metrics["success_probability_v2"])
            mlflow.log_metrics(metrics)
            mlflow.set_tag("project", project_name)
            mlflow.set_tag("risk_level", risk_level)
            mlflow.set_tag("type", "evaluation")
    except Exception as e:
        _log_mlflow_failure("log_evaluation", e)


def _registry_api():
    """The MLflow entry points `log_model_registry` uses, resolved per call.

    A seam, not an abstraction. It exists so a test can substitute MLflow
    *before* a client is built and before a connection is opened -- patching
    `requests` underneath a client that has already resolved its tracking URI
    tests the transport, not this function, and leaves the interesting failure
    (the artefact upload being refused) unreachable.

    Resolved on each call rather than captured at import, so nothing here holds
    module-level mutable state that one test could leak into the next.
    """
    return mlflow


def log_model_registry(model, model_name: str, metrics: dict, *, api=None) -> bool:
    """Register the model, and **say whether it worked** (#189).

    This returned None either way, so a caller could not tell a registered
    model from one whose upload was refused. Measured on production
    2026-08-15: registration had been failing with
    `PermissionError: '/mlflow'` while every retrain reported success, because
    experiments 0 and 1 carry an absolute `artifact_location` from before the
    server ran with `--serve-artifacts`.

    Returns True when the model reached the registry, False when it did not,
    and False when MLflow is switched off -- because "we did not try" is also
    "it is not registered", and a caller deciding whether to promote needs the
    second fact, not the first.

    Still does not raise. Telemetry must not take a completed retrain down with
    it; what changes is that the outcome is now reported rather than swallowed.

    `api` is the injection seam described on `_registry_api`. Callers in the
    application never pass it, so production resolves to the `mlflow` module and
    makes exactly the calls it made before.
    """
    if _OFFLINE:
        return False
    api = _registry_api() if api is None else api
    _ensure_experiment_once(api)
    try:
        with api.start_run(run_name=f"register_{model_name}"):
            api.log_metrics(metrics)
            api.sklearn.log_model(model, model_name)
            api.set_tag("type", "model_registry")
        return True
    except Exception as e:
        _log_mlflow_failure("log_model_registry", e)
        return False


def get_experiment_stats():
    """Latest successful retrain metrics, read from the configured database.

    This used to open `sqlite3.connect("data/sora.db")` -- a hardcoded relative
    path that ignored DATABASE_URL entirely (#137). Production runs PostgreSQL,
    so it created an empty SQLite file, failed to find `retrain_log` in it, and
    the handler below turned that into the default response. The endpoint has
    been reporting no metrics regardless of what the real table held.

    Two outcomes that used to look identical are now distinguished. "The query
    ran and found nothing" is a fact about the data; "the query did not run" is
    a fact about the system, and reporting the second as the first is what let
    this last.
    """
    # Reads the database for metrics, but also queries MLflow below.
    _ensure_experiment_once()
    import json

    from sqlalchemy.exc import SQLAlchemyError

    from app.database import RetrainLog, SessionLocal

    result = {
        "experiment": EXPERIMENT_NAME,
        "tracking_uri": MLFLOW_TRACKING_URI,
        "total_runs": 0,
    }
    row = None
    try:
        # The configured session, so one code path serves PostgreSQL and
        # SQLite. Nothing here needs a raw connection: `retrain_log` is a
        # mapped model.
        with SessionLocal() as db:
            row = (
                db.query(RetrainLog.metrics_json, RetrainLog.model_version,
                         RetrainLog.started_at)
                .filter(RetrainLog.status == "success",
                        RetrainLog.metrics_json.isnot(None),
                        RetrainLog.metrics_json != "")
                .order_by(RetrainLog.started_at.desc())
                .first()
            )
    except SQLAlchemyError:
        # Logged with a traceback, and reported as unavailable rather than as
        # zero runs. The previous version put `str(e)` into the response, which
        # both misreported the state and leaked database details to a public
        # endpoint.
        logger.exception("get_experiment_stats: retrain_log query failed")
        result["_retrain_log"] = "unavailable"
    else:
        if row is None:
            result["_retrain_log"] = "no successful runs"

    try:
        if row and row[0]:
            m = json.loads(row[0])
            roc = m.get("roc_auc") or m.get("auc")
            result["roc_auc"] = roc
            result["ensemble_cv_auc"] = m.get("ensemble_cv_auc") or roc
            result["rf_cv_auc"] = m.get("rf_cv_auc") or roc
            result["xgb_cv_auc"] = m.get("xgb_cv_auc") or roc
            result["f1_score"] = m.get("f1_score") or m.get("f1")
            result["accuracy"] = m.get("accuracy")
            result["best_f1"] = m.get("best_f1")
            result["best_threshold"] = m.get("best_threshold")
            result["train_samples"] = m.get("train_samples")
            result["test_samples"] = m.get("test_samples")
            result["model_version"] = row[1] or ""
            result["last_retrain_at"] = str(row[2])
            result["_source"] = "retrain_log"
    except (ValueError, TypeError, KeyError):
        # Parsing the stored JSON, not reaching the database. Kept narrow: a
        # bare `except Exception` here is what previously absorbed the
        # connection failure as well and reported it as zero runs.
        logger.exception("get_experiment_stats: metrics_json could not be parsed")
        result["_retrain_log"] = "metrics unreadable"
    if not _OFFLINE:
        try:
            experiment = mlflow.get_experiment_by_name(EXPERIMENT_NAME)
            if experiment:
                runs = mlflow.search_runs(experiment_ids=[experiment.experiment_id], max_results=100)
                result["total_runs"] = len(runs)
        except Exception as e:
            # total_runs stays 0 either way. The warning is deliberately the only
            # signal: this dict is returned verbatim by GET /api/v1/mlflow/stats
            # (app/api/infra.py), so adding a field here would change a public
            # response and leak an internal exception class to clients.
            _log_mlflow_failure("get_experiment_stats", e)
    return result





def log_drift_event(analysis_result, baseline_id="default"):
    if _OFFLINE:
        return None
    """Log drift detection event to MLflow.

    Stores PSI/KS metrics per feature, drift_score, drifted features.
    Tag type=drift_event for filtering in /drift/mlflow-history.
    """
    _ensure_experiment_once()
    if not analysis_result or not analysis_result.get("drift_detected"):
        return
    try:
        from datetime import datetime as _dt
        run_name = "drift_" + _dt.now().strftime("%Y%m%d_%H%M%S")
        with mlflow.start_run(run_name=run_name):
            mlflow.set_tag("type", "drift_event")
            mlflow.set_tag("baseline_id", str(baseline_id))

            metrics = {
                "drift_score": float(analysis_result.get("drift_score", 0.0) or 0.0),
                "drifted_features_count": float(len(analysis_result.get("drifted_features", []) or [])),
                "n_samples_ref": float(analysis_result.get("reference_samples", 0) or 0),
                "n_samples_cur": float(analysis_result.get("current_samples", 0) or 0),
            }
            psi = analysis_result.get("psi") or {}
            for feat, m in psi.items():
                if isinstance(m, dict) and m.get("psi") is not None:
                    safe = str(feat).replace(" ", "_")[:40]
                    metrics["psi_" + safe] = float(m["psi"])
            ks = analysis_result.get("ks_test") or analysis_result.get("ks") or {}
            for feat, m in ks.items():
                if isinstance(m, dict) and m.get("p_value") is not None:
                    safe = str(feat).replace(" ", "_")[:40]
                    metrics["ks_pvalue_" + safe] = float(m["p_value"])
            mlflow.log_metrics(metrics)

            drifted = analysis_result.get("drifted_features", []) or []
            mlflow.log_param("drifted_features", ",".join(str(x) for x in drifted)[:250])
            feats = analysis_result.get("features_analyzed", []) or []
            mlflow.log_param("features_analyzed", ",".join(str(x) for x in feats)[:250])
    except Exception as _e:
        try:
            print("[mlflow_drift] log failed:", _e)
        except Exception:
            pass
