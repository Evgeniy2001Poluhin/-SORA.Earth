"""Load the champion from one source, whole (#191, roadmap phase 3).

`model_source.py` decides *which* directory. This loads from it, and the rule it
enforces is that the answer applies to the entire set:

    model.pkl + scaler.pkl + best_threshold.pkl + meta.json + metrics.json

all from `active`, or all from `seed`. Never one file from each. A model and a
scaler from different training runs produce predictions that are wrong in a way
nothing downstream can detect: the features are scaled into a space the model
was not fitted on, the probabilities stay in [0, 1], and every check passes.

Presence is not enough to guarantee that. `resolve_model_source` checks the
files exist; a pickle can still be truncated or from an incompatible version, so
the load itself is transactional — everything is read into locals first, and only
a complete set is published. A failure part-way through `active` discards what
was read and falls back to `seed` **as a whole**.

## What the champion is, and is not

The champion is the RandomForest set above, which is what retraining produces.
`xgb_model.pkl`, `stacking_meta.pkl`, `ensemble_model_v2*.pkl`, `scaler_v2.pkl`
and `cat_encodings.json` are **different models**, not parts of this one. They
are not retrained, they have no staged or active copy, and they continue to load
from the seed directory. That is not the mixing this module forbids — mixing is
taking one model's pieces from two places — but it is worth stating, because
"loads from one source" would otherwise sound like a claim about every file in
`models/`.

## When recovery runs

`recover()` resolves an activation interrupted by a crash, and it takes a lock
and may rename directories. It runs **once at process start**, and again before
an explicit reload. It does not run per prediction: a prediction uses the set
already loaded, and doing filesystem repair on a request path would put a lock
acquisition in front of every inference.
"""
import json
import logging
import os
import pickle
from dataclasses import dataclass
from typing import Any, Optional

from app.model_source import ModelSource, recover, resolve_model_source
from app.paths import active_dir, seed_dir

logger = logging.getLogger(__name__)

#: Read together or not at all.
CHAMPION_ARTEFACTS = ("model.pkl", "scaler.pkl", "best_threshold.pkl", "meta.json")


@dataclass
class Champion:
    """The serving model, and the provenance that travels with it."""

    model: Any
    scaler: Any
    best_threshold: float
    meta: dict
    metrics: dict
    source: ModelSource


def _load_set(directory: str) -> Champion:
    """Read the whole set from one directory, or raise having published nothing."""
    with open(os.path.join(directory, "model.pkl"), "rb") as handle:
        model = pickle.load(handle)
    with open(os.path.join(directory, "scaler.pkl"), "rb") as handle:
        scaler = pickle.load(handle)
    with open(os.path.join(directory, "best_threshold.pkl"), "rb") as handle:
        threshold = pickle.load(handle)["threshold"]
    with open(os.path.join(directory, "meta.json"), encoding="utf-8") as handle:
        meta = json.load(handle)

    # Not in CHAMPION_ARTEFACTS: a run that reports no metrics is still a usable
    # model, and refusing to serve it over a missing report would be the wrong
    # trade. Read from the same directory as the rest, so it cannot describe a
    # different model.
    metrics = {}
    metrics_path = os.path.join(directory, "metrics.json")
    if os.path.exists(metrics_path):
        with open(metrics_path, encoding="utf-8") as handle:
            metrics = json.load(handle)

    return Champion(model=model, scaler=scaler, best_threshold=threshold,
                    meta=meta, metrics=metrics,
                    source=ModelSource(source="", version=None))


def load_champion(recover_first: bool = True) -> Champion:
    """The champion, from one source, with the fallback visible.

    `recover_first` defaults to True because the two callers that exist —
    process start and an explicit reload — both want it. A prediction path must
    pass False, or every inference would queue behind a lock.
    """
    if recover_first:
        recover()

    source = resolve_model_source()

    if source.source == "active":
        try:
            champion = _load_set(active_dir())
            champion.source = source
            logger.info("Champion loaded from the active model, run %s, version %s",
                        source.run_id, source.version)
            return champion
        except Exception as exc:
            # Present but unreadable. Falling back is right; falling back
            # silently is not — from outside, a seed model answering looks
            # exactly like a promoted one answering.
            logger.warning(
                "The active model is present but could not be loaded (%s: %s); "
                "serving the seed model instead. Predictions do not come from "
                "the promoted model.", type(exc).__name__, exc,
            )
            source = ModelSource(
                source="seed", version=None, fell_back=True,
                reason=f"the active model could not be loaded: {type(exc).__name__}",
            )

    champion = _load_set(seed_dir())
    champion.source = ModelSource(
        source="seed",
        version=source.version if source.source == "seed" else None,
        fell_back=source.fell_back,
        reason=source.reason,
    )
    if champion.source.fell_back:
        logger.warning("Serving the seed model: %s", champion.source.reason)
    return champion


def health_fields(source: ModelSource) -> dict:
    """What `/health` may say about the model's provenance.

    Never a path. `/health` is reachable without authentication, and the
    filesystem layout of the host is not something an unauthenticated response
    should publish.
    """
    return {
        "source": source.source,
        "run_id": source.run_id,
        "model_version": source.version,
        "fell_back": source.fell_back,
        "reason": source.reason,
    }
