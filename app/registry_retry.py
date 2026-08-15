"""Register an already-trained model again, without retraining it (#189).

A run that trained a good model and failed only to register it currently has no
route back: the model is on disk and in memory, the gate refused promotion, and
the only way to get a registered model is to train another one. That throws away
work for a reason that has nothing to do with the model.

**The identity this uses already exists.** `_do_retrain` writes
`models/meta.json` with `retrained_at`, and records the same string as
`RetrainLog.model_version`. Nothing was invented for this; the pairing is what
makes the retry safe rather than merely possible.

**And it is the limit of what can be retried.** The artefacts are written to
fixed names -- `model.pkl`, `scaler.pkl`, `meta.json` -- so the next retrain
overwrites them. There is no per-run copy, which means the model produced by run
N is *gone* once run N+1 finishes. This module therefore refuses rather than
guesses: if `meta.json` no longer names the run being retried, the model that run
produced cannot be recovered, and registering whatever happens to be on disk
would file one model under another's identity. That refusal is the honest answer,
not a gap to be filled by picking the newest artefact.

Only `_do_retrain` records `model_version`; the closed loop's own journal row
leaves it NULL. So retries key on the training row, which is the row that
carries both the version and `registry_ok`.
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

#: Never registered, and nothing was changed. Each says which of the two is
#: true -- "it did not work" and "there is nothing here to work on" call for
#: different responses.
NOT_FOUND = "not_found"
NO_MODEL_VERSION = "no_model_version"
ARTIFACT_MISSING = "artifact_missing"
ARTIFACT_SUPERSEDED = "artifact_superseded"
REGISTRATION_FAILED = "registration_failed"
#: Registered now, or registered already.
REGISTERED = "registered"
ALREADY_REGISTERED = "already_registered"


def _result(outcome, retrain_log_id, detail, **extra):
    payload = {"outcome": outcome, "retrain_log_id": retrain_log_id, "detail": detail}
    payload.update(extra)
    return payload


def retry_registration(retrain_log_id: int, *, models_dir=None, api=None, session_factory=None):
    """Re-register the model that `retrain_log_id` produced, if it is still the one on disk.

    Idempotent through the journal: a run already marked `registry_ok` is left
    alone and reported `already_registered`, so calling twice does not put a
    second version of one model into the registry.

    That guarantee is the journal's, not MLflow's, and it has one honest gap:
    if the upload succeeded but the process died before the journal was written,
    the row still says False and a retry would register the model a second time.
    Closing that needs the registry queried for the version, which needs a
    version to query by -- and naming versions is the thing this deliberately
    does not invent.
    """
    from app.database import RetrainLog, SessionLocal
    from app.mlflow_tracking import log_model_registry

    if models_dir is None:
        from app.paths import models_dir as _models_dir
        models_dir = _models_dir()

    db = (session_factory or SessionLocal)()
    try:
        row = db.query(RetrainLog).filter(RetrainLog.id == retrain_log_id).first()
        if row is None:
            return _result(NOT_FOUND, retrain_log_id, "no retrain_log row with that id")

        if not row.model_version:
            # The closed loop's own row never carries one; the training row does.
            return _result(
                NO_MODEL_VERSION, retrain_log_id,
                "this row records no model_version, so no artefact can be matched to it",
            )

        try:
            metrics = json.loads(row.metrics_json) if row.metrics_json else {}
        except (TypeError, ValueError):
            metrics = {}
        if not isinstance(metrics, dict):
            metrics = {}

        if metrics.get("registry_ok") is True:
            return _result(
                ALREADY_REGISTERED, retrain_log_id,
                "the journal already records this run as registered",
                model_version=row.model_version,
            )

        meta_path = os.path.join(models_dir, "meta.json")
        model_path = os.path.join(models_dir, "model.pkl")
        if not os.path.exists(meta_path) or not os.path.exists(model_path):
            return _result(
                ARTIFACT_MISSING, retrain_log_id,
                "no model.pkl/meta.json on disk to register",
                model_version=row.model_version,
            )

        try:
            with open(meta_path) as handle:
                on_disk = (json.load(handle) or {}).get("retrained_at")
        except (OSError, ValueError) as exc:
            return _result(
                ARTIFACT_MISSING, retrain_log_id,
                f"meta.json could not be read: {type(exc).__name__}",
                model_version=row.model_version,
            )

        if on_disk != row.model_version:
            # A later retrain has overwritten the fixed filenames. The model this
            # run produced no longer exists, and the one that does belongs to a
            # different run.
            return _result(
                ARTIFACT_SUPERSEDED, retrain_log_id,
                "a later retrain overwrote the artefacts; this run's model is gone",
                model_version=row.model_version, on_disk_version=on_disk,
            )

        import pickle
        with open(model_path, "rb") as handle:
            model = pickle.load(handle)

        registered = log_model_registry(
            model, "RandomForest_retrain",
            {k: v for k, v in metrics.items()
             if isinstance(v, (int, float)) and not isinstance(v, bool)},
            api=api,
        )

        if not registered:
            # The journal is not touched: it already says False, and writing
            # False over False would move `finished_at` for a run that did not
            # change.
            return _result(
                REGISTRATION_FAILED, retrain_log_id,
                "MLflow refused the model again; the journal is unchanged",
                model_version=row.model_version,
            )

        metrics["registry_ok"] = True
        row.metrics_json = json.dumps(metrics, ensure_ascii=False)
        db.commit()
        logger.info(
            "Registry retry: run %s (%s) is now registered", retrain_log_id, row.model_version
        )
        return _result(
            REGISTERED, retrain_log_id, "registered from the existing artefact",
            model_version=row.model_version,
        )
    finally:
        db.close()
