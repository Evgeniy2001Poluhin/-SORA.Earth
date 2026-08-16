"""Which model is served, where it came from, and how it is replaced (#191).

Three locations, decided by the owner:

    models/                     seed — the bootstrap that ships in Git LFS
    runtime/staged/<run_id>/    a candidate, before any gate has ruled
    runtime/active/             the champion, switched into place atomically

## Why a candidate is never served

`resolve_model_source` does not look in `staged/` at all. A rejected candidate
must never become the serving model, and the cheapest guarantee is that the
serving path has no way to reach one. `activate` is the only writer of
`active/`, so promotion is an act someone performed — today the serving model is
replaced by the fact of training, before any gate has ruled.

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

## What this does not do yet

The seed files have not moved into `models/seed/`. `.gitattributes` tracks
`models/*.pkl`, a pattern that is not recursive, so relocating them drops them
out of LFS and a fresh clone fails on a missing artefact — which closed PR #196.
Nothing calls this module yet either; wiring the loader is a separate slice.
"""
import contextlib
import fcntl
import logging
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from typing import List, Optional

from app.paths import STAGED_RETENTION, active_dir, runtime_dir, seed_dir, staged_dir

logger = logging.getLogger(__name__)

#: A directory is a usable model only when all of these are present. Checking
#: one file was not enough: a copy interrupted after `model.pkl` and before
#: `scaler.pkl` would have passed, and the features would then be scaled by
#: whatever the previous model's scaler happened to be.
REQUIRED_ARTEFACTS = ("model.pkl", "scaler.pkl", "best_threshold.pkl", "meta.json")

#: Written into a staged directory while it is still being assembled, and
#: removed when it is complete. Pruning and activation both refuse a directory
#: carrying it, so a candidate being written cannot be deleted underneath its
#: writer or promoted half-finished.
INCOMPLETE_MARKER = ".incomplete"

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
    #: `meta.json`'s `retrained_at`, when there is one. None for a seed that
    #: never recorded one, which is a fact about the artefact, not an error.
    version: Optional[str] = None
    #: True when active existed but was unusable, so seed answered instead.
    fell_back: bool = False
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
        return ModelSource(source="active", version=_version_of(active))

    seed = seed_dir()
    existed = os.path.isdir(active)
    reason = None
    if existed:
        missing = _missing(active)
        reason = (
            "the active model is incomplete: missing " + ", ".join(missing)
            if missing else "the active model is still being written"
        )
        # A warning, not an error: falling back is the designed behaviour.
        # Being quiet about it is the fault — from outside, a seed model
        # answering looks exactly like a promoted one answering.
        logger.warning(
            "Serving the seed model: %s. Predictions do not come from the "
            "promoted model.", reason
        )
    return ModelSource(source="seed", version=_version_of(seed),
                       fell_back=existed, reason=reason)


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
    return ModelSource(source="active", version=_version_of(active))


def prune_staged(keep: int = STAGED_RETENTION, protect=()) -> List[str]:
    """Drop the oldest candidates, keeping the most recent `keep`.

    Four things are never removed, whatever their age:

    - a directory still being written (`INCOMPLETE_MARKER`);
    - the candidate the current `active` was built from, matched by version, so
      a rollback still has something to roll back to;
    - anything named in `protect` — the run being activated, or one a retry is
      about to re-register;
    - nothing at all, if the staged root does not exist.

    Retention is by modification time; run ids are UUIDs and carry no order.
    """
    root = os.path.join(runtime_dir(), "staged")
    if not os.path.isdir(root):
        return []

    protected = {str(p) for p in protect}
    active_version = _version_of(active_dir())

    with _exclusive():
        entries = []
        for name in os.listdir(root):
            path = os.path.join(root, name)
            if not os.path.isdir(path):
                continue
            if os.path.exists(os.path.join(path, INCOMPLETE_MARKER)):
                continue
            if name in protected:
                continue
            if active_version is not None and _version_of(path) == active_version:
                continue
            entries.append((os.path.getmtime(path), name))

        entries.sort(reverse=True)
        removed = []
        for _, name in entries[max(keep, 0):]:
            shutil.rmtree(os.path.join(root, name))
            removed.append(name)

    return removed
