"""Retraining: assemble a candidate model in runtime/staged/<run_id>/ and journal it.

This is the part of retraining the scheduler calls. It does not promote anything
(see app/model_loader.py activate_promoted_candidate) and it does not serve HTTP.

It must not import app.api or app.auth. The scheduler runs in its own container
without SORA_JWT_SECRET, and app.auth refuses to import in production without
it, so anything reached from here through app.api would fail the scheduler's
first real retrain. app/api/retrain.py re-exports _do_retrain for the routes.

Path constants are read here at call time from this module's globals. A test
that wants to redirect the dataset must patch this module, not app.api.retrain.
"""
import hashlib
import json
import logging
import os
import pickle
from datetime import datetime

import numpy as np
import pandas as pd
from fastapi import HTTPException
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score

from app.model_source import INCOMPLETE_MARKER, discard_unfinished, prune_staged
from app.paths import data_dir, staged_dir
from app.scheduler import _start_retrain_log, _finish_retrain_log, new_run_id

logger = logging.getLogger("sora_earth")

PRED_LOG = os.path.join(data_dir(), "predictions_log.csv")
PROJECTS_CSV = os.path.join(data_dir(), "projects.csv")


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

    # Retention is enforced here because every candidate is created here, by
    # whichever caller asked for the run (#191). `prune_staged` existed and
    # nothing called it, so every run's directory stayed. Before this run's own
    # directory exists, so it is never among what gets pruned. Reported, not
    # raised: a directory that cannot be removed must not stop every retrain
    # after it.
    try:
        prune_staged()
    except Exception as exc:
        logger.warning(
            "retrain: staged candidates were not pruned, so retention is not "
            "being enforced: %s: %s", type(exc).__name__, exc,
        )

    # The candidate's directory, created before anything is written into it, so
    # every write below addresses one place. The marker says "still being
    # assembled": activation refuses a directory carrying it and pruning leaves
    # one alone until `ABANDONED_AFTER`, so a half-written candidate can be
    # neither promoted nor deleted underneath its writer.
    staged = staged_dir(run_id)
    os.makedirs(staged, exist_ok=True)
    with open(os.path.join(staged, INCOMPLETE_MARKER), "w") as _marker:
        _marker.write(run_id)

    #: None until the dataset has been read. The failure path below reports it
    #: either way, and a run that fell over before reaching the file genuinely
    #: has no dataset to name -- which is a different fact from one that read a
    #: dataset and failed afterwards, and the row should be able to say which.
    data_version = None

    try:
        if not os.path.exists(PROJECTS_CSV):
            raise HTTPException(400, "No training data (projects.csv) found")

        #: Which bytes this run trained on (#164 line of work; the
        #: `data snapshot -> run_id` link of the roadmap's definition of done).
        #:
        #: Of the file, not of the parsed frame: pandas' reading is a second
        #: thing that can change between releases, and the question this answers
        #: is "did two runs see the same dataset", which is about the input.
        #:
        #: The dataset is not static -- `POST /model/data/bulk-upload/content`
        #: replaces it atomically, and the comment below records that it is
        #: appended to by more than that path -- so before this, two champions
        #: trained a week apart were indistinguishable in the record except by
        #: `total_samples` and a timestamp.
        with open(PROJECTS_CSV, "rb") as _handle:
            data_version = "sha256:" + hashlib.sha256(_handle.read()).hexdigest()

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
            # `total_samples` is the count after inf/NaN rows are dropped, so it
            # is not the size of the file. Both are recorded: the digest names
            # the dataset, the counts say how much of it was usable.
            "data_version": data_version,
            "data_rows_read": int(before),
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
            #: Returned so a caller writing a second row for the same physical
            #: run can put the same value in it rather than re-digesting a file
            #: that may have changed since. `retrain_models` and
            #: `closed_loop_retrain` both write such a row and neither passes it
            #: yet, so their rows still carry NULL -- the remaining half of this
            #: link. This comment first said it "belongs with the one-row-per-run
            #: work (#199)"; #199 was closed COMPLETED on 2026-09-19 and its
            #: closing note says the unification must be filed fresh. No open
            #: issue carries it, so this half is unowned rather than scheduled.
            "data_version": data_version,
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
            data_version=data_version,
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
        # A failed run removes what it started: left behind, its marker kept the
        # directory out of pruning for good. Before the journal row, so a
        # database error there cannot skip it, and never raised, so it cannot
        # replace the failure being reported. A process killed outright runs
        # none of this; `ABANDONED_AFTER` in app.model_source is for that one.
        try:
            discard_unfinished(run_id)
        except Exception as cleanup:
            logger.warning(
                "retrain: the unfinished candidate %s was not removed: %s: %s",
                run_id, type(cleanup).__name__, cleanup,
            )
        _finish_retrain_log(
            log_id=log_id,
            data_version=data_version,
            training_status="failed",
            failure_reason=type(e).__name__,
            message="Manual retraining failed",
            error_message=str(e),
        )
        raise
