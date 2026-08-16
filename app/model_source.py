"""Which model is being served, and where it came from (#191).

Three locations, decided by the owner:

    models/            seed — the bootstrap that ships in Git LFS
    runtime/staged/<run_id>/   a candidate, before any gate has ruled
    runtime/active/    the champion, switched into place atomically

## Why the order is active, then seed

Losing the runtime volume must not take the platform down; it must fall back to
the models the repository ships, and say so. Falling back silently is the part
that would be wrong -- an operator seeing predictions has no way to tell that
they come from a bootstrap artefact rather than the model that was promoted, and
that difference is the whole content of the last six months of retraining.

## Why a candidate is never served

`resolve_model_source` does not look in `staged/` at all. A rejected candidate
must never become the serving model, and the cheapest way to guarantee that is
for the serving path to have no way to reach one. Promotion is a separate,
explicit act -- `activate` -- which is the only thing that writes `active/`.

## What this does not do yet

The seed files have not moved into `models/seed/`. `.gitattributes` tracks
`models/*.pkl` and that pattern is not recursive, so relocating them drops them
out of LFS and a fresh clone fails on a missing artefact -- which is what closed
PR #196. `seed_dir()` therefore still resolves to `models/`, and the move is a
separate mechanical commit against a mechanism already proven here.
"""
import logging
import os
import shutil
from dataclasses import dataclass
from typing import Optional

from app.paths import STAGED_RETENTION, active_dir, seed_dir, staged_dir

logger = logging.getLogger(__name__)

#: A directory is a usable model only if this is in it. Anything less is a
#: half-written copy, and serving from one is worse than falling back.
REQUIRED_ARTEFACT = "model.pkl"


@dataclass
class ModelSource:
    """Where the served model came from, for `/health` and for the log."""

    #: `active` or `seed`.
    source: str
    path: str
    #: `meta.json`'s `retrained_at`, when there is one. None for a seed that
    #: never recorded it, which is a fact about the artefact and not an error.
    version: Optional[str] = None
    #: True when active exists but was unusable, so seed answered instead. The
    #: operator needs this: predictions are being served from the bootstrap.
    fell_back: bool = False
    reason: Optional[str] = None


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


def _is_usable(path: str) -> bool:
    return os.path.isdir(path) and os.path.exists(os.path.join(path, REQUIRED_ARTEFACT))


def resolve_model_source() -> ModelSource:
    """Active if it is usable, otherwise seed -- and never a staged candidate."""
    active = active_dir()
    if _is_usable(active):
        return ModelSource(source="active", path=active, version=_version_of(active))

    seed = seed_dir()
    reason = (
        f"{active} has no {REQUIRED_ARTEFACT}" if os.path.isdir(active)
        else f"{active} does not exist"
    )
    fell_back = os.path.isdir(active)
    if fell_back:
        # Only a warning: falling back is the designed behaviour, not a fault.
        # Being quiet about it is the fault.
        logger.warning(
            "Serving the seed model: %s. Predictions do not come from the "
            "promoted model.", reason
        )
    return ModelSource(source="seed", path=seed, version=_version_of(seed),
                       fell_back=fell_back, reason=reason if fell_back else None)


def activate(run_id: str) -> ModelSource:
    """Make a staged candidate the champion, atomically.

    A rename is atomic within a filesystem; copying into `active/` is not, and a
    reader arriving mid-copy would load half a model. The new directory is put
    in place beside the old one and swapped, so at no instant does `active/`
    hold a partial artefact.

    This is the only function that writes `active/`. Promotion has to be an act
    someone performed, not a side effect of training -- today the serving model
    is replaced by the fact of training, before any gate has ruled (#199).
    """
    staged = staged_dir(run_id)
    if not _is_usable(staged):
        raise ValueError(f"staged candidate {run_id} has no {REQUIRED_ARTEFACT}: {staged}")

    active = active_dir()
    os.makedirs(os.path.dirname(active), exist_ok=True)
    incoming = active + ".incoming"
    previous = active + ".previous"

    for stale in (incoming, previous):
        if os.path.exists(stale):
            shutil.rmtree(stale)

    shutil.copytree(staged, incoming)
    if os.path.exists(active):
        os.rename(active, previous)
    os.rename(incoming, active)
    if os.path.exists(previous):
        shutil.rmtree(previous)

    logger.info("Activated model from run %s", run_id)
    return ModelSource(source="active", path=active, version=_version_of(active))


def prune_staged(keep: int = STAGED_RETENTION) -> list:
    """Drop the oldest candidates, keeping the most recent `keep`.

    Returns what was removed. Retention is by modification time rather than by
    run id, because ids are uuids and carry no order.
    """
    root = os.path.join(os.path.dirname(active_dir()), "staged")
    if not os.path.isdir(root):
        return []

    entries = [
        (os.path.getmtime(os.path.join(root, name)), name)
        for name in os.listdir(root)
        if os.path.isdir(os.path.join(root, name))
    ]
    entries.sort(reverse=True)

    removed = []
    for _, name in entries[max(keep, 0):]:
        shutil.rmtree(os.path.join(root, name))
        removed.append(name)
    return removed
