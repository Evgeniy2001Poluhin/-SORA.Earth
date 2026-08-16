from fastapi import Depends
from app.auth import require_admin
"""Model retraining and metrics API."""
import contextlib
import fcntl
import os, csv, pickle, json, stat, tempfile, time
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score, classification_report
import torch

from fastapi import (APIRouter, BackgroundTasks, Depends, File, HTTPException,
                     UploadFile)
from app.auth import require_api_key
from app.paths import data_dir, models_dir, staged_dir
from app.model_source import INCOMPLETE_MARKER

router = APIRouter(prefix="/model", tags=["mlops"])

from app.scheduler import _start_retrain_log, _finish_retrain_log, new_run_id
logger = __import__("logging").getLogger("sora_earth")

PRED_LOG = os.path.join(data_dir(), "predictions_log.csv")
PROJECTS_CSV = os.path.join(data_dir(), "projects.csv")
MODELS_DIR = models_dir()
# Bulk upload reads only from here; the caller never supplies an absolute path.
UPLOADS_DIR = os.environ.get("SORA_UPLOADS_DIR", os.path.join(data_dir(), "uploads"))
MAX_UPLOAD_BYTES = int(os.environ.get("SORA_MAX_UPLOAD_BYTES", 50 * 1024 * 1024))
# Same root as PROJECTS_CSV: the atomic replace below needs the temp file and
# the target on one filesystem, and the lock has to sit beside what it guards.
DATASET_LOCK = os.path.join(data_dir(), ".projects.csv.lock")



@router.get("/metrics")
def model_metrics():
    metrics_path = os.path.join(MODELS_DIR, "metrics.json")
    if not os.path.exists(metrics_path):
        raise HTTPException(404, "No metrics file found")
    with open(metrics_path) as f:
        metrics = json.load(f)
    meta_path = os.path.join(MODELS_DIR, "meta.json")
    meta = {}
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)
    return {
        "metrics": metrics,
        "meta": meta,
        "models_available": [f for f in os.listdir(MODELS_DIR) if f.endswith(('.pkl', '.pth'))],
    }


@router.get("/status")
def model_status():
    from app.main import best_threshold, model_meta
    from app.database import SessionLocal, RetrainLog
    import json

    db = SessionLocal()
    try:
        rows = (
            db.query(RetrainLog)
            .order_by(RetrainLog.started_at.desc())
            .limit(10)
            .all()
        )
        history = [
            {
                "status": r.status,
                "trigger_source": r.trigger_source,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "duration_sec": r.duration_sec,
                "model_version": r.model_version,
                "metrics": json.loads(r.metrics_json) if r.metrics_json else None,
            }
            for r in rows
        ]
    finally:
        db.close()

    return {
        "current_threshold": best_threshold,
        "meta": model_meta,
        "retrain_history": history,
        "prediction_log_size": _count_predictions(),
    }


def _get_current_metrics():
    """Return current model metrics from latest RetrainLog or MLflow."""
    try:
        from app.database import SessionLocal, RetrainLog
        from sqlalchemy import desc
        import json
        db = SessionLocal()
        try:
            row = db.query(RetrainLog).filter(
                RetrainLog.status == "success",
                RetrainLog.metrics_json.isnot(None),
            ).order_by(desc(RetrainLog.finished_at)).first()
            if row and row.metrics_json:
                return json.loads(row.metrics_json)
        finally:
            db.close()
    except Exception:
        pass
    return {}


def walk_forward_validate(model_class, X, y, n_splits=5):
    """Walk-forward CV for time series. Returns mean/std AUC across folds."""
    from sklearn.model_selection import TimeSeriesSplit

    tscv = TimeSeriesSplit(n_splits=n_splits)
    scores = []
    try:
        for train_idx, test_idx in tscv.split(X):
            X_tr, X_te = X[train_idx], X[test_idx]
            y_tr, y_te = y[train_idx], y[test_idx]
            m = model_class()
            m.fit(X_tr, y_tr)
            if hasattr(m, "predict_proba"):
                y_pred = m.predict_proba(X_te)[:, 1]
            else:
                y_pred = m.predict(X_te)
            auc = roc_auc_score(y_te, y_pred)
            scores.append(auc)
    except Exception:
        return {"mean_auc": None, "std_auc": None, "folds": 0}

    return {
        "mean_auc": float(np.mean(scores)),
        "std_auc": float(np.std(scores)),
        "folds": len(scores)
    }


def _do_retrain(min_samples: int = 50, trigger_source: str = "manual"):
    """Actual retrain logic with persisted RetrainLog."""
    #: Minted here because this run writes one row and its caller writes another
    #: for the same physical retrain (#199 phase 2A).
    run_id = new_run_id()
    log_id = _start_retrain_log(trigger_source=trigger_source, job_name="model_retrain",
                                run_id=run_id)

    # The candidate's directory, created before anything is written into it, so
    # every write below addresses one place. The marker says "still being
    # assembled": activation and pruning both refuse a directory carrying it, so
    # a half-written candidate can be neither promoted nor deleted underneath
    # its writer.
    staged = staged_dir(run_id)
    os.makedirs(staged, exist_ok=True)
    with open(os.path.join(staged, INCOMPLETE_MARKER), "w") as _marker:
        _marker.write(run_id)

    try:
        if not os.path.exists(PROJECTS_CSV):
            raise HTTPException(400, "No training data (projects.csv) found")

        df = pd.read_csv(PROJECTS_CSV)
        required = ["budget", "co2_reduction", "social_impact", "duration_months", "success"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise HTTPException(400, f"Missing columns in projects.csv: {missing}")

        if os.path.exists(PRED_LOG):
            try:
                log_df = pd.read_csv(PRED_LOG)
                enrichment_count = len(log_df) if len(log_df) > 0 and "prediction" in log_df.columns else 0
            except Exception:
                enrichment_count = 0
        else:
            enrichment_count = 0

        #: What the recorded metrics were measured under. Stored beside them, so
        #: a number in retrain_log says which question it answers -- and so the
        #: series does not silently mix two kinds when the temporal split
        #: becomes possible.
        SPLIT_KIND = "stratified_by_outcome"

        min_samples = max(10, min(min_samples, 100000))  # safety clamp
        if len(df) < min_samples:
            raise HTTPException(400, f"Need at least {min_samples} samples, have {len(df)}")

        from datetime import datetime as _dt
        df["budget_per_month"] = df["budget"] / df["duration_months"].clip(lower=1)
        df["co2_per_dollar"]   = df["co2_reduction"] / df["budget"].clip(lower=1) * 1000
        df["efficiency_score"] = (df["co2_reduction"] * df["social_impact"]) / df["duration_months"].clip(lower=1)
        df["year"]             = _dt.utcnow().year
        df["quarter"]          = (_dt.utcnow().month - 1) // 3 + 1

        feature_cols = ["budget", "co2_reduction", "social_impact", "duration_months",
                        "budget_per_month", "co2_per_dollar", "efficiency_score",
                        "year", "quarter"]

        # NaN and inf are removed before anything is fitted (#1).
        #
        # Without this the failure is `ValueError: Input y contains NaN` from
        # deep inside sklearn, which names neither the row nor the column. A
        # missing `success` is the common case -- projects.csv is appended to
        # by more than the validated upload path -- and inf arrives from the
        # derived features when a value is large enough to overflow the
        # division rather than from a zero denominator, which the clips above
        # already prevent.
        #
        # The count is reported rather than dropped quietly. Training on fewer
        # rows than the operator supplied is a fact about the model, and a
        # silent filter is how a dataset shrinks over releases with nobody
        # noticing.
        before = len(df)
        df = df.replace([np.inf, -np.inf], np.nan).dropna(
            subset=feature_cols + ["success"])
        dropped = before - len(df)
        if dropped:
            logger.warning(
                "retrain: dropped %d of %d rows carrying NaN or inf in a "
                "feature or the label", dropped, before,
            )

        # Re-checked after the drop, not only before it. The count that matters
        # is how many rows can be trained on, and the earlier check was made
        # against rows that included the unusable ones.
        if len(df) < min_samples:
            raise HTTPException(
                400,
                f"Need at least {min_samples} usable samples, have {len(df)}"
                + (f" ({dropped} of {before} rows carried NaN or inf)"
                   if dropped else ""),
            )

        X = df[feature_cols].values

        # Checked, not coerced. `astype(int)` on its own turns 0.9 into 0 and
        # 1.9 into 1, so a mislabelled row trains the model on a value nobody
        # wrote -- silently, and differently from the CSV it came from.
        labels = df["success"]
        off_scale = labels[~labels.isin([0, 1])]
        if len(off_scale) > 0:
            raise HTTPException(
                400,
                f"{len(off_scale)} rows have a success value that is not 0 or 1 "
                f"(first: {off_scale.iloc[0]!r})",
            )
        # int, not whatever read_csv inferred: a `success` column holding one
        # NaN is read as float64, and the classifier then trains on 0.0/1.0
        # labels while `stratify` and the metrics expect discrete classes.
        y = labels.values.astype(int)

        # One class cannot be scored. `roc_auc_score` raises "Only one class
        # present in y_true", which reads as a bug in the metric rather than as
        # a description of the training set.
        classes = sorted(set(y.tolist()))
        if len(classes) < 2:
            raise HTTPException(
                400,
                f"Training data has only one outcome class ({classes}); "
                f"a classifier needs both successes and failures",
            )

        # Stratified by outcome, and **not** a temporal split (#183).
        #
        # This used to slice at 80% of the row order and call itself temporal.
        # projects.csv has no time column: the order is whatever was appended
        # last, by bulk upload or by /data/refresh. Measured 2026-08-14, the two
        # sides were different populations -- 75.9% successes in the first 80%
        # against 39.6% in the last 20%, with median budget 40M against 30M and
        # median duration 69 against 49 months.
        #
        # So every AUC this recorded measured a shift in population as much as
        # a model, and the promotion gate compared two such numbers.
        #
        # Stratifying removes the grossest artefact -- the class balance is now
        # the same on both sides -- at the cost of the property the old name
        # claimed. **This does not measure learning from the past.** A row from
        # any point in the file may land in either side. That limitation is
        # stated here, and in SPLIT_KIND below, rather than left for a reader to
        # infer from a name.
        #
        # The honest temporal split needs an observation time per row, which
        # nothing has ever written. Rows created from here carry `recorded_at`;
        # the 17,071 already in the file cannot get one retroactively, so a
        # temporal split becomes possible on new history and never on this
        # history -- the same shape as #164.
        from sklearn.model_selection import train_test_split

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y,
        )
        logger.info(
            "Stratified split: train=%d (%.3f positive), test=%d (%.3f positive)",
            len(X_train), float(y_train.mean()), len(X_test), float(y_test.mean()),
        )

        # Both classes on both sides, checked after the split rather than only
        # over the whole set.
        #
        # The split is temporal and does not shuffle, so a dataset ordered by
        # outcome satisfies the global check and still puts every failure in
        # the first 80% and every success in the last 20%. `rf.fit` then trains
        # a one-class model and `predict_proba(...)[:, 1]` indexes a column
        # that does not exist -- an IndexError far from the cause.
        for part, labels_part in (("train", y_train), ("test", y_test)):
            if len(set(labels_part.tolist())) < 2:
                raise HTTPException(
                    400,
                    f"The temporal split leaves one outcome class in {part} "
                    f"({sorted(set(labels_part.tolist()))}); the data is "
                    f"ordered by outcome and cannot be split this way",
                )

        # Walk-forward cross-validation
        wf = walk_forward_validate(RandomForestClassifier, X, y)
        logger.info("Walk-forward CV: mean_auc=%.4f std=%.4f folds=%d",
                    wf["mean_auc"] or 0, wf["std_auc"] or 0, wf["folds"])

        from sklearn.preprocessing import StandardScaler
        scaler = StandardScaler()
        X_train_s = scaler.fit_transform(X_train)
        X_test_s = scaler.transform(X_test)

        rf = RandomForestClassifier(n_estimators=200, max_depth=10, random_state=42, n_jobs=-1)
        rf.fit(X_train_s, y_train)

        from sklearn.calibration import CalibratedClassifierCV
        rf_cal = CalibratedClassifierCV(rf, cv="prefit", method="isotonic")
        rf_cal.fit(X_test_s, y_test)
        with open(os.path.join(staged, "rf_model_cal.pkl"), "wb") as fc:
            pickle.dump(rf_cal, fc)

        y_pred = rf.predict(X_test_s)
        y_proba = rf.predict_proba(X_test_s)[:, 1]

        acc = round(accuracy_score(y_test, y_pred), 4)
        f1 = round(f1_score(y_test, y_pred), 4)
        try:
            auc = round(roc_auc_score(y_test, y_proba), 4)
        except Exception:
            auc = None

        best_t, best_f1 = 0.5, f1
        for t in np.arange(0.3, 0.8, 0.01):
            preds_t = (y_proba >= t).astype(int)
            f1_t = f1_score(y_test, preds_t)
            if f1_t > best_f1:
                best_f1 = round(f1_t, 4)
                best_t = round(t, 2)

        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

        # The candidate goes to runtime/staged/<run_id>/, never to models/
        # (#191, #199 phase 4). models/ is the immutable seed, and writing the
        # newly trained model there overwrote the bootstrap every run — the
        # thing a lost runtime volume is supposed to fall back to.
        #
        # It is also no longer served by being written. Until a gate rules, this
        # directory is a candidate and nothing loads from it.
        with open(os.path.join(staged, "model.pkl"), "wb") as f:
            pickle.dump(rf, f)
        with open(os.path.join(staged, "scaler.pkl"), "wb") as f:
            pickle.dump(scaler, f)
        with open(os.path.join(staged, "best_threshold.pkl"), "wb") as f:
            pickle.dump({"threshold": best_t}, f)

        new_metrics = {
            "accuracy": acc, "f1_score": f1, "best_f1": best_f1,
            "roc_auc": auc, "best_threshold": best_t,
            "train_samples": len(X_train), "test_samples": len(X_test),
            "enrichment_from_log": enrichment_count,
            # Which question the numbers above answer. Without it the series in
            # retrain_log silently mixes two kinds the day the temporal split
            # becomes possible, and a comparison across that boundary would be
            # a comparison of two different measurements.
            "split_kind": SPLIT_KIND,
            # The class counts of the test set, not only its size. A confidence
            # bound on AUC needs both -- the estimate's precision depends on how
            # many of each there were, and 171 rows split 120/51 is a different
            # measurement from 171 split 5/166.
            "test_positive": int((y_test == 1).sum()),
            "test_negative": int((y_test == 0).sum()),
        }
        with open(os.path.join(staged, "metrics.json"), "w") as f:
            json.dump(new_metrics, f, indent=2)

        new_meta = {
            "retrained_at": timestamp,
            "algorithm": "RandomForestClassifier",
            "n_estimators": 200, "max_depth": 10,
            "features": feature_cols, "total_samples": len(df),
        }
        with open(os.path.join(staged, "meta.json"), "w") as f:
            json.dump(new_meta, f, indent=2)

        # Complete. Only now may a gate promote it.
        os.remove(os.path.join(staged, INCOMPLETE_MARKER))

        # The serving model is NOT replaced here (#199 phase 4).
        #
        # This block used to assign rf_model, scaler and the threshold straight
        # into app.main, so a model became the champion by the fact of having
        # been trained — before the AUC gate, the confidence bound, the registry
        # check or the degradation check had run. "Not promoted" then meant "it
        # is serving, and the journal says it should not be".
        #
        # Promotion is now an act: the gate calls activate(run_id), and the
        # reload happens there.
        reloaded = False

        result = {
            "status": "success",
            "metrics": new_metrics,
            "meta": new_meta,
            "models_reloaded": reloaded,
            "timestamp": timestamp,
            #: The row this run owns (#199 phase 0). Callers that finalise a
            #: decision must address it by id: `app/api/infra.py` used to find
            #: "the newest row with trigger_source='mlops_auto'", which finalises
            #: a concurrent run's row instead of its own.
            "retrain_log_id": log_id,
            #: Same physical run as the decision row the caller will write.
            "run_id": run_id,
            #: The candidate is staged under this run and is not serving. A
            #: caller that decides to promote calls activate(run_id).
            "staged": True,
        }

        try:
            from app.mlflow_tracking import log_model_registry
            registry_ok = log_model_registry(
                rf, "RandomForest_retrain",
                {"auc": auc or 0, "f1": f1, "accuracy": acc})
            new_metrics["registry_ok"] = bool(registry_ok)
        except Exception as exc:
            # Absent is not the same as False, and the difference decides
            # promotion: a run with no `registry_ok` is treated as recorded
            # before the contract existed and is promoted, so swallowing this
            # turned an import failure into a silent approval (#199 phase 0).
            #
            # `log_model_registry` does not raise -- everything outside its own
            # try cannot -- so in practice this catches the import above. It is
            # still written as a general handler because the cost of being wrong
            # about that is a promotion nobody chose.
            new_metrics["registry_ok"] = False
            new_metrics["registry_error"] = type(exc).__name__
            logger.error(
                "Registry step failed for model %s: %s: %s -- recording it as "
                "unregistered rather than leaving the field absent",
                timestamp, type(exc).__name__, exc,
            )


        _finish_retrain_log(
            log_id=log_id,
            # No `status`: it is derived from the stages by project_status, so
            # this row cannot disagree with its own fields (#199 phase 2B).
            training_status="success",
            registry_status=(
                "registered" if new_metrics.get("registry_ok") is True
                else "failed" if new_metrics.get("registry_ok") is False
                else None
            ),
            message="Manual retraining completed successfully",
            model_version=timestamp,
            metrics=new_metrics,
        )
        return result

    except Exception as e:
        logger.exception("Retrain failed in _do_retrain: %s", e)
        _finish_retrain_log(
            log_id=log_id,
            training_status="failed",
            failure_reason=type(e).__name__,
            message="Manual retraining failed",
            error_message=str(e),
        )
        raise

#: When a row entered the training set, stamped as it is appended.
#:
#: An honest temporal split needs a time per row and nothing ever wrote one.
#: The "temporal split" this file used to do sliced the row order instead, and
#: measured a shift in population (#183).
#:
#: **Rows already in the file cannot get one.** All 17,071 of them predate this
#: and there is no record of when each arrived; inventing a value would be the
#: retroactive provenance #164 exists to undo. So the column is empty for the
#: history and filled from here on, and a temporal split becomes possible on
#: new rows and never on the old ones.
#:
#: A reader computing a temporal split must therefore check how much of the
#: column is filled. A split over a half-filled column silently selects the new
#: rows, which is a population, not a period.
RECORDED_AT = "recorded_at"


def _stamped(df):
    """A copy of `df` carrying the moment it entered the training set."""
    stamped = df.copy()
    stamped[RECORDED_AT] = datetime.utcnow().isoformat(timespec="seconds")
    return stamped


def _preflight_retrain(min_samples: int) -> int:
    """Refuse, before accepting, what can be refused without training (#2).

    These are the same checks `_do_retrain` makes; the difference is when. In
    the background they arrive after the caller has been told `accepted` and
    the response has been sent -- so the 400 has nobody to reach, and raising
    it there produced `RuntimeError: Caught handled exception, but response
    already started` in the ASGI layer.

    Returns the clamped `min_samples` so the caller and the job agree on the
    number the refusal was measured against.
    """
    if not os.path.exists(PROJECTS_CSV):
        raise HTTPException(400, "No training data (projects.csv) found")

    clamped = max(10, min(min_samples, 100000))
    try:
        df = pd.read_csv(PROJECTS_CSV)
    except Exception as e:
        raise HTTPException(400, f"projects.csv is unreadable: {type(e).__name__}")

    required = ["budget", "co2_reduction", "social_impact", "duration_months", "success"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise HTTPException(400, f"Missing columns in projects.csv: {missing}")
    if len(df) < clamped:
        raise HTTPException(400, f"Need at least {clamped} samples, have {len(df)}")
    return clamped


def _retrain_in_background(min_samples: int) -> None:
    """Run the job with nothing left to raise into.

    A background task has no response to attach a status to. `HTTPException`
    from here is a category error -- there is nobody to receive a 400 -- and
    Starlette turns it into a RuntimeError that reads as a server fault rather
    than as a refused retrain.

    `_do_retrain` already logs the reason and closes its RetrainLog row on the
    way out, so the outcome is recorded where an operator looks for it. What
    this adds is that it stops here.
    """
    try:
        _do_retrain(min_samples, trigger_source="manual")
    except HTTPException as exc:
        logger.error("background retrain refused: %s", exc.detail)
    except Exception:
        logger.exception("background retrain failed")


@router.post("/retrain")
def retrain_model(background_tasks: BackgroundTasks, current_user=Depends(require_admin), min_samples: int = 50, sync: bool = False):
    """Retrain RF. Default=async, ?sync=true for synchronous.

    The cheap preconditions are checked before the job is accepted, so a
    request that cannot succeed is refused to the caller rather than accepted
    and abandoned (#2).
    """
    clamped = _preflight_retrain(min_samples)
    if sync:
        return _do_retrain(clamped)
    background_tasks.add_task(_retrain_in_background, clamped)
    return {
        "status": "accepted",
        "message": "Retrain started in background",
        "check_status": "/model/status",
    }


@router.get("/feature-importance")
def feature_importance():
    from app.main import rf_model, FEATURE_COLS
    importances = rf_model.feature_importances_
    pairs = sorted(zip(FEATURE_COLS, importances.tolist()), key=lambda x: -x[1])
    return {"features": [{"name": n, "importance": round(v, 4)} for n, v in pairs]}


@router.get("/prediction-log/stats")
def prediction_log_stats():
    if not os.path.exists(PRED_LOG):
        return {"total": 0, "file_exists": False}
    try:
        df = pd.read_csv(PRED_LOG)
        return {
            "total": len(df),
            "columns": list(df.columns),
            "file_exists": True,
            "file_size_kb": round(os.path.getsize(PRED_LOG) / 1024, 1),
        }
    except Exception as e:
        return {"total": 0, "error": str(e)}


def _count_predictions() -> int:
    if not os.path.exists(PRED_LOG):
        return 0
    try:
        with open(PRED_LOG) as f:
            return sum(1 for _ in f) - 1
    except Exception:
        return 0


@router.post("/data/refresh")
def data_refresh(
    budget: float,
    co2_reduction: float,
    social_impact: float,
    duration_months: int,
    success: int,
    auto_retrain_threshold: int = 20, current_user=Depends(require_admin),
):
    """Append a labeled data point. Auto-retrain when threshold reached."""
    if success not in (0, 1):
        raise HTTPException(400, "success must be 0 or 1")

    new_row = {
        "budget": budget,
        "co2_reduction": co2_reduction,
        "social_impact": social_impact,
        "duration_months": duration_months,
        "success": success,
        "name": f"auto_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
        "category": "Unknown",
        "region": "Unknown",
    }

    df_existing = pd.read_csv(PROJECTS_CSV)
    df_new = pd.concat(
        [df_existing, _stamped(pd.DataFrame([new_row]))], ignore_index=True)
    df_new.to_csv(PROJECTS_CSV, index=False)

    # Read last retrain total_samples from RetrainLog
    from app.database import SessionLocal, RetrainLog
    _db = SessionLocal()
    try:
        _last = _db.query(RetrainLog).filter(RetrainLog.status == 'success').order_by(RetrainLog.started_at.desc()).first()
        if _last and _last.metrics_json:
            _m = json.loads(_last.metrics_json)
            last_retrain_samples = _m.get('train_samples', 0) + _m.get('test_samples', 0)
        else:
            last_retrain_samples = 0
    finally:
        _db.close()
    new_since_retrain = len(df_new) - last_retrain_samples

    triggered = False
    retrain_result = None
    if new_since_retrain >= auto_retrain_threshold:
        try:
            retrain_result = _do_retrain(min_samples=50, trigger_source="auto")
            triggered = True
        except Exception as e:
            retrain_result = {"error": str(e)}

    return {
        "status": "added",
        "total_samples": len(df_new),
        "new_since_last_retrain": new_since_retrain,
        "auto_retrain_triggered": triggered,
        "retrain_result": retrain_result,
    }


def _safe_for_log(value: str, limit: int = 200) -> str:
    """Neutralise caller-supplied text before it reaches an audit line.

    file_path comes from the request, so a newline in it would let a caller
    forge additional audit entries. Non-printable characters are replaced rather
    than stripped, so tampering stays visible, and a leading spreadsheet formula
    trigger is quoted in case the log is later opened in a spreadsheet.
    """
    cleaned = "".join(ch if ch.isprintable() else "?" for ch in value[:limit])
    if cleaned[:1] in ("=", "+", "-", "@"):
        cleaned = "'" + cleaned
    return cleaned


def _resolve_upload_path(file_path: str) -> str:
    """Resolve file_path inside UPLOADS_DIR, rejecting anything that escapes it.

    Kept for callers that only need the resolved name. Reading goes through
    _open_upload, which anchors on a directory descriptor instead.
    """
    candidate = os.path.realpath(os.path.join(UPLOADS_DIR, file_path))
    root = os.path.realpath(UPLOADS_DIR)
    if candidate != root and not candidate.startswith(root + os.sep):
        raise HTTPException(400, "file_path must stay inside the uploads directory")
    return candidate


def _open_upload(file_path: str) -> int:
    """Open the upload once, anchored to UPLOADS_DIR, and return the descriptor.

    Resolving a path and then opening it by name is a time-of-check/time-of-use
    gap: between the two, the name can be repointed at another file. Everything
    downstream therefore reads from this one descriptor, never from the path
    again.

    Anchoring uses dir_fd, so the lookup is performed relative to an already
    opened UPLOADS_DIR and cannot be redirected by replacing the directory
    itself. O_NOFOLLOW refuses a symlink as the final component.

    Known limitation: O_NOFOLLOW guards only the last component. An intermediate
    directory in a nested path could still be a symlink. Closing that fully needs
    openat2(RESOLVE_BENEATH), which CPython does not expose; until then, keep
    UPLOADS_DIR writable only by the service account.
    """
    if os.path.isabs(file_path):
        raise HTTPException(400, "file_path must be relative to the uploads directory")
    parts = [p for p in file_path.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise HTTPException(400, "file_path must stay inside the uploads directory")
    if not parts:
        raise HTTPException(400, "file_path must name a file")

    try:
        dir_fd = os.open(UPLOADS_DIR, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        raise HTTPException(400, "uploads directory is unavailable")

    try:
        try:
            fd = os.open(
                os.path.join(*parts),
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=dir_fd,
            )
        except FileNotFoundError:
            raise HTTPException(400, f"File not found: {file_path}")
        except OSError:
            # ELOOP when the final component is a symlink, EACCES, and so on.
            raise HTTPException(400, f"File is not readable: {file_path}")
    finally:
        os.close(dir_fd)

    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise HTTPException(400, "file_path must name a regular file")
        if st.st_size > MAX_UPLOAD_BYTES:
            raise HTTPException(
                413, f"File exceeds the {MAX_UPLOAD_BYTES} byte upload limit"
            )
        if st.st_size == 0:
            raise HTTPException(400, "File is empty")
    except HTTPException:
        os.close(fd)
        raise
    except OSError:
        os.close(fd)
        raise HTTPException(400, "File could not be inspected")

    return fd


def _replace_projects_csv(df_merged) -> None:
    """Replace the training set atomically, so a failure cannot truncate it.

    Written to a temporary file in the same directory -- therefore the same
    filesystem, which os.replace requires -- then flushed, fsynced and renamed.
    The directory is fsynced afterwards so the rename itself survives a crash.
    """
    directory = os.path.dirname(PROJECTS_CSV)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".projects-", suffix=".csv")
    try:
        with os.fdopen(fd, "w", newline="") as tmp:
            df_merged.to_csv(tmp, index=False)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_path, PROJECTS_CSV)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    try:
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        # Not every filesystem allows fsync on a directory; the rename is still
        # atomic, only its durability across a crash is weaker.
        pass


@contextlib.contextmanager
def _dataset_lock():
    """Serialise read-modify-write on projects.csv across processes."""
    os.makedirs(os.path.dirname(DATASET_LOCK), exist_ok=True)
    fd = os.open(DATASET_LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise HTTPException(409, "Another upload is in progress; retry shortly")
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _ingest_frame(df_new, auto_retrain: bool, _audit) -> dict:
    """Validate, merge and persist -- the half that does not depend on how the
    bytes arrived.

    Shared by both upload routes on purpose. When this was inlined in one of
    them, adding the second meant copying the validation, and a copy is how two
    endpoints come to disagree about what a valid row is.
    """
    required = ["budget", "co2_reduction", "social_impact", "duration_months", "success"]
    missing = [c for c in required if c not in df_new.columns]
    if missing:
        _audit("rejected_missing_columns", detail=",".join(missing))
        raise HTTPException(400, f"Missing columns: {missing}")
    if len(df_new) == 0:
        _audit("rejected_no_rows")
        raise HTTPException(400, "CSV contains no rows")
    invalid = df_new[~df_new["success"].isin([0, 1])]
    if len(invalid) > 0:
        _audit("rejected_invalid_rows", rows=len(invalid))
        raise HTTPException(400, f"{len(invalid)} rows have invalid success values (must be 0 or 1)")

    # Validation is complete; only now is the dataset touched, and only under the
    # lock so two uploads cannot interleave their read-modify-write.
    with _dataset_lock():
        df_existing = pd.read_csv(PROJECTS_CSV)
        df_merged = pd.concat([df_existing, _stamped(df_new)], ignore_index=True)
        try:
            _replace_projects_csv(df_merged)
        except Exception as e:
            _audit("failed_write", rows=len(df_new), detail=type(e).__name__)
            raise HTTPException(500, "Failed to persist the dataset; it is unchanged")

    _audit("uploaded", rows=len(df_new))
    result = {
        "status": "uploaded",
        "rows_added": len(df_new),
        "total_samples": len(df_merged),
    }

    # Retraining runs only after the replacement is durable.
    if auto_retrain:
        try:
            retrain_result = _do_retrain(min_samples=50, trigger_source="auto")
            result["retrain"] = retrain_result["metrics"]
            _audit("retrain_ok", rows=len(df_new))
        except Exception as e:
            # The dataset write already succeeded, so this is reported, not rolled back.
            result["retrain_error"] = type(e).__name__
            _audit("retrain_failed", rows=len(df_new), detail=type(e).__name__)
    return result


#: Read size for the upload stream. Small enough that the limit below is
#: overshot by at most this much, large enough not to syscall per byte.
UPLOAD_CHUNK_BYTES = 64 * 1024


def _stream_to_temp(upload: UploadFile) -> str:
    """Write an uploaded body to a file this process names, and stop at the limit.

    #26. The endpoint below takes a *path on the server* from the caller.
    PR #24 made that path safe -- admin-only, anchored on a directory
    descriptor, O_NOFOLLOW, size-checked, atomic, locked, audited -- but the
    shape stayed: the API asks a remote caller to name a file in a filesystem
    the caller cannot see, which means the bytes had to arrive by some other
    channel that nothing here audits.

    Two properties this has and that one could not:

    the caller names nothing
        `tempfile.mkstemp` chooses the name, in a directory this module owns.
        There is no component of the path the request can influence, so
        symlink resolution, `..`, and the shared uploads directory all stop
        being questions.

    the limit is enforced while reading
        `os.fstat` could only reject a file that had already arrived. Here the
        counter is checked per chunk and the read stops, so the disk holds at
        most `MAX_UPLOAD_BYTES + UPLOAD_CHUNK_BYTES` before the refusal -- not
        whatever the client chose to send.

    The partial file is removed on every failure path. Returning the name of a
    file that may not exist is worse than raising.
    """
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=UPLOADS_DIR, prefix=".upload-", suffix=".csv")
    written = 0
    try:
        with os.fdopen(fd, "wb") as sink:
            while True:
                chunk = upload.file.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        413,
                        f"Upload exceeds the {MAX_UPLOAD_BYTES} byte limit",
                    )
                sink.write(chunk)
        if written == 0:
            raise HTTPException(400, "Upload is empty")
    except BaseException:
        # Includes the 413 above: a refused upload must not leave its prefix
        # behind for the next caller to find under a name nobody recorded.
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise
    return tmp_path


@router.post("/data/bulk-upload", deprecated=True)
def data_bulk_upload(
    file_path: str,
    auto_retrain: bool = False,
    _admin=Depends(require_admin),
):
    """**Deprecated.** Use `POST /model/data/bulk-upload/content` instead.

    Upload CSV with columns: budget,co2_reduction,social_impact,duration_months,success

    Takes a path on the server, which means the bytes had to arrive by some
    other channel first -- one nothing here audits. PR #24 made the path safe
    to open; it could not make the shape safe, because the shape is "a remote
    caller names a file in a filesystem it cannot see".

    Kept, and marked, for one release: the audit line this writes is what will
    show whether anyone is still calling it (#26). Removal is on that evidence,
    not on a guess.

    Appends to the training set and can trigger a retrain, so it is admin-only,
    reads only from UPLOADS_DIR, and replaces the dataset atomically under an
    interprocess lock. Every outcome is audited.
    """
    actor = _safe_for_log(str(getattr(_admin, "username", None) or "admin"), 64)
    safe_name = _safe_for_log(file_path)
    started = datetime.utcnow().isoformat()

    def _audit(outcome: str, rows: int = 0, detail: str = "") -> None:
        # Deliberately no server paths: the caller only ever learns the name it
        # supplied, never where the uploads directory lives.
        logger.info(
            "bulk_upload actor=%s action=data_bulk_upload file=%s rows=%d "
            "auto_retrain=%s result=%s started_at=%s%s",
            actor, safe_name, rows, auto_retrain, outcome, started,
            f" detail={detail}" if detail else "",
        )

    fd = _open_upload(file_path)
    try:
        with os.fdopen(fd, "rb") as handle:
            try:
                df_new = pd.read_csv(handle)
            except Exception as e:
                _audit("rejected_unparseable", detail=type(e).__name__)
                raise HTTPException(400, f"CSV parse error: {type(e).__name__}")
    except HTTPException:
        raise

    return _ingest_frame(df_new, auto_retrain, _audit)

@router.post("/data/bulk-upload/content")
def data_bulk_upload_content(
    file: UploadFile = File(...),
    auto_retrain: bool = False,
    _admin=Depends(require_admin),
):
    """Upload CSV content, not a path to it (#26).

    The body carries the bytes. Nothing in the request names a location on the
    server, so the whole class of question the deprecated route raised --
    which directory, whose symlink, how did the file get there -- does not
    arise. The limit is enforced while the stream is read rather than after it
    has landed.

    Validation, atomic replacement, the interprocess lock and the audit line
    are the same as the deprecated route: they are one function, not a copy.

    The client's filename is recorded for traceability and is never used to
    open anything. It is attacker-controlled text, so it goes through the same
    sanitiser as everything else that reaches a log line.
    """
    actor = _safe_for_log(str(getattr(_admin, "username", None) or "admin"), 64)
    safe_name = _safe_for_log(file.filename or "(unnamed)")
    started = datetime.utcnow().isoformat()

    def _audit(outcome: str, rows: int = 0, detail: str = "") -> None:
        logger.info(
            "bulk_upload actor=%s action=data_bulk_upload_content file=%s rows=%d "
            "auto_retrain=%s result=%s started_at=%s%s",
            actor, safe_name, rows, auto_retrain, outcome, started,
            f" detail={detail}" if detail else "",
        )

    try:
        tmp_path = _stream_to_temp(file)
    except HTTPException as exc:
        _audit("rejected_upload", detail=str(exc.status_code))
        raise

    try:
        try:
            df_new = pd.read_csv(tmp_path)
        except Exception as e:
            _audit("rejected_unparseable", detail=type(e).__name__)
            raise HTTPException(400, f"CSV parse error: {type(e).__name__}")
        return _ingest_frame(df_new, auto_retrain, _audit)
    finally:
        # The temporary file has no reader after this call. Leaving it would
        # rebuild the shared mutable directory this endpoint exists to remove.
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
