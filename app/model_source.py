"""Which model is served, where it came from, and how it is replaced (#191).

Three locations, decided by the owner:

    models/                     seed — the bootstrap that ships in Git LFS
    runtime/staged/<run_id>/    a candidate, before any gate has ruled
    runtime/active/             the champion, switched into place atomically

## Why a candidate is never served

`resolve_model_source` does not look in `staged/` at all. A rejected candidate
must never become the serving model, and the cheapest guarantee is that the
serving path has no way to reach one. `activate` is the only writer of
`active/`, so promotion is an act someone performed. Until #199 phase 4 it was
not: `_do_retrain` assigned the new model into `app.main`, and the serving model
was replaced by the fact of training, before any gate had ruled.

## Crash safety

Activation moves through named states so that an interruption at any point
leaves exactly one recoverable arrangement, and `recover()` resolves each:

    build     active.next assembled and fsynced   -> next present, active present
    swap 1    active -> active.previous            -> previous present, active gone
    swap 2    active.next -> active                -> previous present, active present
    cleanup   active.previous removed              -> steady state

`rename` is atomic only within a filesystem, so `active.next` and
`active.previous` are siblings of `active` under the same runtime root, never in
a temp directory elsewhere. The data is fsynced — files and the directory — before
the swap: a rename that is atomic to the filesystem is not durable to the disk
on its own, and a power loss between the two would otherwise leave a directory
entry pointing at unwritten bytes.

## Locking

The backend and the scheduler are separate processes in separate containers.
Two activations racing would interleave their renames and produce a state no
recovery could distinguish, so every mutation takes an exclusive `flock` on a
file under the runtime root, and pruning takes it too.

## Across containers

`flock` excludes two processes only when both open **the same inode**. The
subprocess test in `tests/test_model_source.py` proves the lock works between
processes in one environment; it cannot prove it works between containers. This
module landed before anything mounted a shared runtime, so it named four things
the wiring would have to establish. Where each stands:

- the backend and the scheduler mount one runtime volume — established.
  `docker-compose.prod.yml` mounts the named volume `runtime_models` at
  `/app/runtime` in both, and `tests/test_runtime_volume_is_shared.py` refuses
  two different sources or a bind mount;
- both resolve the same `SORA_RUNTIME_DIR` — established. Both set
  `/app/runtime`, checked by the same file;
- the lock file is one inode — follows from the two above, and nothing observes
  it: those tests read the compose file, not a running host;
- a check demonstrating mutual exclusion across the two containers — **not
  established**. No test, CI job or deployment step starts both containers and
  contends for the lock, so the exclusion rests on the host running what the
  compose file declares.

Every worker takes the lock as it starts, so the volume must also be writable by
the containers' user: `Dockerfile.prod` creates `/app/runtime` so that Docker
initialises an empty volume with that owner (#213).

`docker-compose.yml`, the development file, declares no runtime volume and no
`SORA_RUNTIME_DIR`. There `app` and `scheduler` each keep `runtime/` inside their
own container: they share no lock, and the app never sees a model the scheduler
activates.

## What calls this

When this module landed nothing called it, and this docstring concluded that a
defect here could not change what production does. The loader (#207) and the
gate (#209) ended that. The callers are listed by

    grep -rn "model_source import" app/ --exclude=model_source.py

and when this was written they were:

- `app.model_loader.load_champion` calls `recover()` and then
  `resolve_model_source()`, and `app.main` calls it at import. gunicorn is not
  given `--preload`, so each backend worker imports the app itself and runs
  recovery — a lock, and possibly renames and deletions under the runtime root —
  as it starts.
- `app.scheduler.closed_loop_retrain` promotes through
  `app.model_loader.activate_promoted_candidate`, which is the only caller of
  `activate(run_id)`, and which then calls `app.model_loader.reload_champion`.
  It is not the only caller of *that helper*: `app.api.infra.auto_retrain_on_drift`
  (`POST /api/v1/mlops/auto-retrain`) reaches it the same way, after the same
  gate. Two gated paths, one door. That reload replaces the model in the
  process that ran the loop and in no other. The scheduled runs are in the
  scheduler container, so the backend's workers keep the champion they loaded
  until each starts again; a run triggered over HTTP reloads only the worker
  that took the request.
- `app.api.retrain._do_retrain` assembles every candidate in `staged_dir(run_id)`
  under `INCOMPLETE_MARKER` and removes the marker once the set is complete.
  Before creating that directory it calls `prune_staged()`, so retention holds
  whichever caller started the run; a run that fails while its marker is still
  there calls `discard_unfinished(run_id)` (#326).
- `app.paths.staged_dir` passes every run id through `validate_run_id` before it
  becomes a directory name.

So a defect in `recover()` runs at the start of every worker, one in
`resolve_model_source()` decides what answers predictions, and one in
`activate()` decides what the closed loop leaves serving.

## What this does not do yet

The seed has not moved into `models/seed/`, and that is no longer pending:
`models/` was declared the seed on 2026-08-16, and `app.paths.seed_dir` records
the measurement behind the decision. `.gitattributes` now carries
`models/**/*.pkl` and `models/**/*.pth` beside `models/*.pkl` (#208), so a
subdirectory added later would stay in LFS — protection, not a plan. The seed is
read-only by enforcement rather than agreement: production mounts `./models`
with `:ro` in both services, and `tests/test_seed_is_immutable.py` refuses a
write to it under `app/` and a writable mount (#321).

Still missing:

- Nothing on this list. Both entries that stood here were done and the heading
  went on presenting them as outstanding, which is the more expensive kind of
  wrong: a list of remaining work is read as current, and each finished entry
  costs a reader the time to discover it is not.

  **Activation on the other gated path** was the first. `POST
  /api/v1/mlops/auto-retrain` now calls `activate_promoted_candidate` and
  reports an activation failure instead of turning it into a rejection; a
  candidate it approves reaches `active/`.

  **A record of which champion a deployment left serving** was the second.
  `scripts/deploy_production.sh` writes an `active_model` line into every
  manifest, beside the commit and the image digests.

  `tests/test_promotion_path_is_described_as_it_is.py` now fails if either
  claim comes back while the code still does the thing, and fails the other way
  if the code stops doing it while this text says it does.
"""
import contextlib
import fcntl
import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from typing import List, Optional

from app.paths import STAGED_RETENTION, active_dir, runtime_dir, seed_dir, staged_dir

logger = logging.getLogger(__name__)

#: A directory is a usable model only when all of these are present. Checking
#: one file was not enough: a copy interrupted after `model.pkl` and before
#: `scaler.pkl` would have passed, and the features would then be scaled by
#: whatever the previous model's scaler happened to be.
#: The single canonical list. `app/model_loader.py` imports it rather than
#: keeping a second one: two independent lists of "what a model is" drift, and
#: the drift shows up as a directory that passes the presence check and then
#: fails to load — the exact confusion this module exists to remove.
REQUIRED_ARTEFACTS = ("model.pkl", "scaler.pkl", "best_threshold.pkl", "meta.json")

#: Read from the same directory when present, but not required: a run that
#: reports no metrics is still a usable model, and refusing to serve it over a
#: missing report would be the wrong trade.
OPTIONAL_ARTEFACTS = ("metrics.json",)

#: Written into a staged directory while it is still being assembled, and
#: removed when it is complete. Activation refuses a directory carrying it, and
#: pruning leaves one alone until `ABANDONED_AFTER`, so a candidate being written
#: cannot be deleted underneath its writer or promoted half-finished.
INCOMPLETE_MARKER = ".incomplete"

#: How long a marker stands before it is read as a writer that died.
#:
#: A retrain removes its marker when it finishes and its whole directory when it
#: fails, so a marker still there is one whose process ran neither -- killed for
#: memory, or in a container recreated mid-run. Nothing else would ever remove
#: that directory.
#:
#: A day, against a marked phase that fits models and makes no network call: the
#: registry step runs after the marker is gone. Being wrong costs a run that
#: really was still writing -- its next write fails and it is recorded as failed.
#: Never reclaiming costs the disk, without limit.
ABANDONED_AFTER = 24 * 60 * 60

#: Written into the champion when it is activated, and carried through the
#: rename with it. It records which run produced the serving model.
#:
#: Protecting a candidate by `meta.json`'s `retrained_at` was too weak: that is
#: a second-resolution timestamp, so two runs finishing in the same second share
#: it, and pruning would then spare the wrong candidate — or fail to spare the
#: right one. `run_id` is already the stronger identity, and `/health`, rollback
#: and the evidence chain all need it anyway.
ACTIVATION_MANIFEST = "activation.json"

_NEXT_SUFFIX = ".next"
_PREVIOUS_SUFFIX = ".previous"


@dataclass
class ModelSource:
    """Where the served model came from.

    Deliberately carries no absolute path: this is what `/health` and the log
    will report, and the filesystem layout of the host is not something a
    response should publish. `source` and `version` identify the artefact;
    `reason` says why a fallback happened without naming a directory.
    """

    #: `active` or `seed`.
    source: str
    #: The run that produced the serving model, when it is known. None for seed
    #: and for a champion activated before manifests existed. Not a path.
    run_id: Optional[str] = None
    #: `meta.json`'s `retrained_at`, when there is one. None for a seed that
    #: never recorded one, which is a fact about the artefact, not an error.
    version: Optional[str] = None
    #: True when active existed but was unusable, so seed answered instead.
    fell_back: bool = False
    #: A stable code, safe to publish: `active_incomplete` or `active_unreadable`.
    #: The list of missing filenames is a detail of the current artefact set and
    #: would change whenever that set changes, so it goes to the server log and
    #: not to an unauthenticated response.
    reason_code: Optional[str] = None
    #: The detailed explanation, for logs. Not published by health_fields.
    reason: Optional[str] = None


def _lock_path() -> str:
    return os.path.join(runtime_dir(), ".model-source.lock")


@contextlib.contextmanager
def _exclusive():
    """One mutation at a time, across processes.

    The backend and the scheduler are separate containers. Two activations
    racing would interleave their renames into a state no recovery could tell
    apart from a crash.
    """
    os.makedirs(runtime_dir(), exist_ok=True)
    handle = os.open(_lock_path(), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(handle, fcntl.LOCK_UN)
        os.close(handle)


def validate_run_id(run_id) -> str:
    """A run id becomes a directory name, so it must be a UUID and nothing else.

    It arrives from a `retrain_log` row. Checking only for separators would
    still admit `.`, `~`, a name that collides with `active`, or a control
    character; parsing it as a UUID admits exactly the shape `new_run_id()`
    produces and refuses everything else.
    """
    if not isinstance(run_id, str):
        raise ValueError(f"run_id must be a string, got {type(run_id).__name__}")
    try:
        parsed = uuid.UUID(run_id)
    except (ValueError, AttributeError, TypeError):
        raise ValueError(f"run_id is not a UUID: {run_id!r}")
    if str(parsed) != run_id.lower():
        raise ValueError(f"run_id is not in canonical UUID form: {run_id!r}")
    return run_id


def _missing(path: str) -> List[str]:
    if not os.path.isdir(path):
        return list(REQUIRED_ARTEFACTS)
    return [name for name in REQUIRED_ARTEFACTS
            if not os.path.exists(os.path.join(path, name))]


def _is_usable(path: str) -> bool:
    return not _missing(path) and not os.path.exists(
        os.path.join(path, INCOMPLETE_MARKER))


def _manifest_of(path: str) -> dict:
    import json

    manifest = os.path.join(path, ACTIVATION_MANIFEST)
    if not os.path.exists(manifest):
        return {}
    try:
        with open(manifest, encoding="utf-8") as handle:
            return json.load(handle) or {}
    except (OSError, ValueError):
        return {}


def _version_of(path: str) -> Optional[str]:
    import json

    meta = os.path.join(path, "meta.json")
    if not os.path.exists(meta):
        return None
    try:
        with open(meta, encoding="utf-8") as handle:
            return (json.load(handle) or {}).get("retrained_at")
    except (OSError, ValueError):
        return None


def _auc_of(path: str) -> Optional[float]:
    """The champion's own AUC, from its `metrics.json`. Both spellings are read."""
    import json

    metrics = os.path.join(path, "metrics.json")
    if not os.path.exists(metrics):
        return None
    try:
        with open(metrics, encoding="utf-8") as handle:
            data = json.load(handle) or {}
    except (OSError, ValueError):
        return None
    value = data.get("auc_roc")
    if value is None:
        value = data.get("roc_auc")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Baseline:
    """What the serving model scored, for the promotion gate to compare against.

    `ok` is the whole point. It is False only when a model is being served and
    its score cannot be established -- the active directory is complete but has
    no `metrics.json`, or its manifest and `meta.json` name different versions.
    A caller must then refuse to promote rather than compare against something
    else: `gated_decision` does exactly that. `ok` is True with `auc=None` only
    for a genuine first model, which has no predecessor to be worse than.
    """

    auc: Optional[float]
    source: str  # "active" | "seed"
    ok: bool
    reason: Optional[str] = None


def serving_baseline() -> Baseline:
    """The AUC of the model that is actually serving -- the champion, or the seed.

    Read from the same place the loader serves from, so the number the gate
    compares against is the number production answers with. Not from
    `retrain_log`: the newest `success` row there is the most recently trained
    candidate, which after a rejection is the rejected model itself (#329).
    """
    active = active_dir()
    if _is_usable(active):
        manifest_version = _manifest_of(active).get("model_version")
        meta_version = _version_of(active)
        if (manifest_version is not None and meta_version is not None
                and manifest_version != meta_version):
            return Baseline(
                None, "active", False,
                "the active model's version disagrees between its activation "
                "manifest (%s) and meta.json (%s)" % (manifest_version, meta_version),
            )
        auc = _auc_of(active)
        if auc is None:
            return Baseline(
                None, "active", False,
                "the active model has no readable metrics to compare against",
            )
        return Baseline(auc, "active", True)

    # Serving the seed. Its metrics may be absent -- a first deployment -- which
    # is not a refusal: there is simply no predecessor.
    return Baseline(_auc_of(seed_dir()), "seed", True)


def _fsync_tree(path: str) -> None:
    """Get the bytes and the directory entries onto the disk before the swap.

    An atomic rename is atomic to the filesystem, not durable to the device. A
    power loss between the copy and the rename would otherwise leave a directory
    entry pointing at bytes that were never written.
    """
    for root, _dirs, files in os.walk(path):
        for name in files:
            handle = os.open(os.path.join(root, name), os.O_RDONLY)
            try:
                os.fsync(handle)
            finally:
                os.close(handle)
        directory = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def _fsync_parent(path: str) -> None:
    handle = os.open(os.path.dirname(path) or ".", os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def recover() -> Optional[str]:
    """Resolve an activation interrupted partway. Returns what was done, or None.

    Each interruption leaves one distinguishable arrangement:

    - `next` present            -> an unfinished build; discard it.
    - `previous` and no `active` -> interrupted between the two renames; the
      previous champion is still intact, so put it back.
    - `previous` and `active`   -> interrupted before cleanup; the swap
      completed, so drop the leftover.

    Putting `previous` back rather than promoting `next` is deliberate: `next`
    was assembled but never chosen, and a crash is not an approval.
    """
    active = active_dir()
    nxt, prev = active + _NEXT_SUFFIX, active + _PREVIOUS_SUFFIX
    outcome = None

    with _exclusive():
        if os.path.exists(nxt):
            shutil.rmtree(nxt)
            outcome = "discarded an unfinished activation"

        if os.path.exists(prev):
            if not os.path.exists(active):
                os.rename(prev, active)
                _fsync_parent(active)
                outcome = "restored the previous model after an interrupted swap"
            else:
                shutil.rmtree(prev)
                outcome = outcome or "removed a leftover previous model"

    if outcome:
        logger.warning("Model activation recovery: %s", outcome)
    return outcome


def resolve_model_source() -> ModelSource:
    """Active if it is complete, otherwise seed — and never a staged candidate."""
    active = active_dir()
    if _is_usable(active):
        return ModelSource(source="active", version=_version_of(active),
                           run_id=_manifest_of(active).get("run_id"))

    seed = seed_dir()
    existed = os.path.isdir(active)
    reason = None
    reason_code = None
    if existed:
        missing = _missing(active)
        reason = (
            "the active model is incomplete: missing " + ", ".join(missing)
            if missing else "the active model is still being written"
        )
        reason_code = "active_incomplete" if missing else "active_being_written"
        # A warning, not an error: falling back is the designed behaviour.
        # Being quiet about it is the fault — from outside, a seed model
        # answering looks exactly like a promoted one answering.
        logger.warning(
            "Serving the seed model: %s. Predictions do not come from the "
            "promoted model.", reason
        )
    return ModelSource(source="seed", version=_version_of(seed),
                       fell_back=existed,
                       reason_code=reason_code if existed else None,
                       reason=reason)


def activate(run_id: str) -> ModelSource:
    """Make a staged candidate the champion, atomically and durably."""
    run_id = validate_run_id(run_id)
    staged = staged_dir(run_id)

    with _exclusive():
        if os.path.exists(os.path.join(staged, INCOMPLETE_MARKER)):
            raise ValueError(f"staged candidate {run_id} is still being written")
        missing = _missing(staged)
        if missing:
            raise ValueError(
                f"staged candidate {run_id} is not a complete model: "
                f"missing {', '.join(missing)}"
            )

        active = active_dir()
        nxt, prev = active + _NEXT_SUFFIX, active + _PREVIOUS_SUFFIX
        os.makedirs(os.path.dirname(active), exist_ok=True)
        for stale in (nxt, prev):
            if os.path.exists(stale):
                shutil.rmtree(stale)

        # build
        shutil.copytree(staged, nxt)
        if _missing(nxt):
            shutil.rmtree(nxt)
            raise ValueError(f"the copy of {run_id} did not come out complete")

        # The manifest goes in before the swap, so it moves with the rename and
        # a champion never exists without a record of which run made it.
        import json
        from datetime import datetime

        with open(os.path.join(nxt, ACTIVATION_MANIFEST), "w", encoding="utf-8") as handle:
            json.dump({
                "run_id": run_id,
                "model_version": _version_of(nxt),
                "activated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            }, handle)

        _fsync_tree(nxt)
        _fsync_parent(nxt)

        # swap
        if os.path.exists(active):
            os.rename(active, prev)
        os.rename(nxt, active)
        _fsync_parent(active)

        # cleanup — after the swap, so an interruption before this leaves
        # `previous` beside a complete `active`, which recover() reads as done.
        if os.path.exists(prev):
            shutil.rmtree(prev)

    logger.info("Activated the model from run %s", run_id)
    return ModelSource(source="active", version=_version_of(active), run_id=run_id)


def prune_staged(keep: int = STAGED_RETENTION, protect=()) -> List[str]:
    """Drop the oldest candidates, keeping the most recent `keep`.

    Never removed, whatever their age:

    - the candidate the current `active` was built from, matched by the run id in
      its activation manifest, so a rollback still has something to roll back to;
    - anything named in `protect` — the run being activated, or one a retry is
      about to re-register;
    - a directory still being written: one carrying `INCOMPLETE_MARKER` that is
      younger than `ABANDONED_AFTER`.

    A marked directory older than that is removed. Marked directories never count
    toward `keep`: none of them is a model anyone could activate. An absent
    staged root is not an error.

    Both ages are the directory's modification time; run ids are UUIDs and carry
    no order.
    """
    root = os.path.join(runtime_dir(), "staged")
    if not os.path.isdir(root):
        return []

    protected = {str(p) for p in protect}

    with _exclusive():
        # Inside the lock. Read before it, an activation that finished while
        # this process waited left its champion unprotected. By run id, not by
        # version: `retrained_at` has second resolution, so two runs finishing
        # in the same second share it and pruning would spare the wrong one.
        champion_run = _manifest_of(active_dir()).get("run_id")
        abandoned_before = time.time() - ABANDONED_AFTER

        entries = []
        removed = []
        for name in os.listdir(root):
            path = os.path.join(root, name)
            if not os.path.isdir(path):
                continue
            if name in protected:
                continue
            if champion_run is not None and name == champion_run:
                continue
            modified = os.path.getmtime(path)
            if os.path.exists(os.path.join(path, INCOMPLETE_MARKER)):
                if modified < abandoned_before:
                    shutil.rmtree(path)
                    removed.append(name)
                    logger.warning(
                        "Removed staged candidate %s: unfinished and untouched "
                        "for over %d hours, so its writer is gone",
                        name, ABANDONED_AFTER // 3600,
                    )
                continue
            entries.append((modified, name))

        entries.sort(reverse=True)
        for _, name in entries[max(keep, 0):]:
            shutil.rmtree(os.path.join(root, name))
            removed.append(name)

    return removed


def discard_unfinished(run_id: str) -> bool:
    """Remove a candidate its own run gave up on. True if anything was removed.

    Only while it still carries `INCOMPLETE_MARKER`. A run that fails after its
    candidate is complete -- in recording the outcome, say -- leaves a whole model,
    and whether to keep that is retention's decision, not this one's.
    """
    staged = staged_dir(run_id)
    with _exclusive():
        if not os.path.exists(os.path.join(staged, INCOMPLETE_MARKER)):
            return False
        shutil.rmtree(staged)
    return True
