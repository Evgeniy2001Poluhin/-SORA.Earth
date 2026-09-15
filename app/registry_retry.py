"""Register an already-trained model again, without retraining it (#189, #327).

A run that trained a good model and failed only to register it -- a transient
MLflow outage, say -- has no route back through the gate: the registry check
refuses it, and the only way to a registered model was to train another one.
That throws away work for a reason that has nothing to do with the model.

**Where the model is.** Since #199 phase 4 the candidate is
`runtime/staged/<run_id>/`, not `models/`. `models/` is the immutable seed,
mounted read-only, and a retrain never writes there -- so reading it (as this
did until #327) could only ever find the seed and refuse. The run id on the
`retrain_log` row names the staged directory directly, so there is no matching
by version and no risk of registering a different run's model under this one's
identity: the directory either is that run's, or it is gone.

**Gone is a real answer.** Retention (`prune_staged`, #326) keeps the five
newest completed candidates and the champion; an older run's candidate may have
been pruned. When the directory is missing, or still carries the
`.incomplete` marker, this refuses rather than guesses. The artefact is read
under the same lock activation and pruning take, so a concurrent prune cannot
remove it between the check and the read; the lock is released before the
registry call, which may be slow.

Only the training row carries `model_version` and `registry_ok`; the closed
loop's own decision row leaves them unset. So a retry targets the training row,
identified by both `model_version` (present) and `run_id` (names the artefact).
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

#: Never registered, and nothing was changed. Each says which of the reasons is
#: true -- they call for different responses.
NOT_FOUND = "not_found"
NO_MODEL_VERSION = "no_model_version"
NO_RUN_ID = "no_run_id"
STAGED_MISSING = "staged_missing"
STILL_WRITING = "still_writing"
REGISTRATION_FAILED = "registration_failed"
#: Registered now, or registered already.
REGISTERED = "registered"
ALREADY_REGISTERED = "already_registered"


def _result(outcome, retrain_log_id, detail, **extra):
    payload = {"outcome": outcome, "retrain_log_id": retrain_log_id, "detail": detail}
    payload.update(extra)
    return payload


def retry_registration(retrain_log_id: int, *, api=None, session_factory=None):
    """Re-register the model that `retrain_log_id` produced, from its staged candidate.

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
    from app.model_source import INCOMPLETE_MARKER, _exclusive, validate_run_id
    from app.paths import staged_dir

    db = (session_factory or SessionLocal)()
    try:
        row = db.query(RetrainLog).filter(RetrainLog.id == retrain_log_id).first()
        if row is None:
            return _result(NOT_FOUND, retrain_log_id, "no retrain_log row with that id")

        if not row.model_version:
            # The closed loop's own row never carries one; the training row does.
            return _result(
                NO_MODEL_VERSION, retrain_log_id,
                "this row records no model_version, so it is not the training row",
            )

        if not row.run_id:
            # A row from before run ids were recorded (#199): the artefact cannot
            # be located, and picking one by any other means is the guess this
            # refuses to make.
            return _result(
                NO_RUN_ID, retrain_log_id,
                "this row records no run_id, so its staged candidate cannot be located",
                model_version=row.model_version,
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
                model_version=row.model_version, run_id=row.run_id,
            )

        try:
            staged = staged_dir(validate_run_id(row.run_id))
        except ValueError:
            return _result(
                NO_RUN_ID, retrain_log_id,
                "the row's run_id is not a valid identifier",
                model_version=row.model_version, run_id=row.run_id,
            )

        # Read the artefact under the activation/pruning lock, so a concurrent
        # prune cannot remove the directory between the check and the read. The
        # lock is released before the registry call below.
        with _exclusive():
            if not os.path.isdir(staged):
                return _result(
                    STAGED_MISSING, retrain_log_id,
                    "the run's staged candidate is gone; it may have been pruned",
                    model_version=row.model_version, run_id=row.run_id,
                )
            if os.path.exists(os.path.join(staged, INCOMPLETE_MARKER)):
                return _result(
                    STILL_WRITING, retrain_log_id,
                    "the candidate is still being written",
                    model_version=row.model_version, run_id=row.run_id,
                )
            model_path = os.path.join(staged, "model.pkl")
            if not os.path.exists(model_path):
                return _result(
                    STAGED_MISSING, retrain_log_id,
                    "the run's staged candidate has no model.pkl",
                    model_version=row.model_version, run_id=row.run_id,
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
                model_version=row.model_version, run_id=row.run_id,
            )

        metrics["registry_ok"] = True
        row.metrics_json = json.dumps(metrics, ensure_ascii=False)
        db.commit()
        logger.info(
            "Registry retry: run %s (%s) is now registered", retrain_log_id, row.model_version
        )
        return _result(
            REGISTERED, retrain_log_id, "registered from the run's staged candidate",
            model_version=row.model_version, run_id=row.run_id,
        )
    finally:
        db.close()
