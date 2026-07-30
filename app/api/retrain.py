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

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from app.auth import require_api_key
from app.paths import data_dir, models_dir

router = APIRouter(prefix="/model", tags=["mlops"])

from app.scheduler import _start_retrain_log, _finish_retrain_log
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
    log_id = _start_retrain_log(trigger_source=trigger_source, job_name="model_retrain")

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

        X = df[feature_cols].values
        y = df["success"].values

        # Temporal split — no shuffling for time series data
        split_idx = int(len(X) * 0.8)
        X_train, y_train = X[:split_idx], y[:split_idx]
        X_test, y_test = X[split_idx:], y[split_idx:]
        logger.info("Temporal split: train=%d, test=%d", len(X_train), len(X_test))

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
        with open(os.path.join(MODELS_DIR, "rf_model_cal.pkl"), "wb") as fc:
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

        with open(os.path.join(MODELS_DIR, "model.pkl"), "wb") as f:
            pickle.dump(rf, f)
        with open(os.path.join(MODELS_DIR, "scaler.pkl"), "wb") as f:
            pickle.dump(scaler, f)
        with open(os.path.join(MODELS_DIR, "best_threshold.pkl"), "wb") as f:
            pickle.dump({"threshold": best_t}, f)

        new_metrics = {
            "accuracy": acc, "f1_score": f1, "best_f1": best_f1,
            "roc_auc": auc, "best_threshold": best_t,
            "train_samples": len(X_train), "test_samples": len(X_test),
            "enrichment_from_log": enrichment_count,
        }
        with open(os.path.join(MODELS_DIR, "metrics.json"), "w") as f:
            json.dump(new_metrics, f, indent=2)

        new_meta = {
            "retrained_at": timestamp,
            "algorithm": "RandomForestClassifier",
            "n_estimators": 200, "max_depth": 10,
            "features": feature_cols, "total_samples": len(df),
        }
        with open(os.path.join(MODELS_DIR, "meta.json"), "w") as f:
            json.dump(new_meta, f, indent=2)

        try:
            from app import main as m
            m.rf_model = rf
            m.scaler = scaler
            m.best_threshold = best_t
            m.model_meta = new_meta
            m.model_metrics = new_metrics
            m.explainer_shap = __import__("shap").TreeExplainer(rf)
            reloaded = True
        except Exception:
            reloaded = False

        result = {
            "status": "success",
            "metrics": new_metrics,
            "meta": new_meta,
            "models_reloaded": reloaded,
            "timestamp": timestamp,
        }

        try:
            from app.mlflow_tracking import log_model_registry
            log_model_registry(rf, "RandomForest_retrain", {"auc": auc or 0, "f1": f1, "accuracy": acc})
        except Exception:
            pass


        _finish_retrain_log(
            log_id=log_id,
            status="success",
            message="Manual retraining completed successfully",
            model_version=timestamp,
            metrics=new_metrics,
        )
        return result

    except Exception as e:
        logger.exception("Retrain failed in _do_retrain: %s", e)
        _finish_retrain_log(
            log_id=log_id,
            status="failed",
            message="Manual retraining failed",
            error_message=str(e),
        )
        raise

@router.post("/retrain")
def retrain_model(background_tasks: BackgroundTasks, current_user=Depends(require_admin), min_samples: int = 50, sync: bool = False):
    """Retrain RF. Default=async, ?sync=true for synchronous."""
    if sync:
        return _do_retrain(min_samples)
    background_tasks.add_task(_do_retrain, min_samples)
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
    df_new = pd.concat([df_existing, pd.DataFrame([new_row])], ignore_index=True)
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


@router.post("/data/bulk-upload")
def data_bulk_upload(
    file_path: str,
    auto_retrain: bool = False,
    _admin=Depends(require_admin),
):
    """Upload CSV with columns: budget,co2_reduction,social_impact,duration_months,success

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
        df_merged = pd.concat([df_existing, df_new], ignore_index=True)
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