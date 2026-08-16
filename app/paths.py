"""Filesystem roots for the artifacts the app reads and writes.

With no environment overrides these resolve to the repository's ``data/`` and
``models/`` directories, which is exactly what the hand-rolled ``os.path``
chains they replace resolved to. Production behaviour is unchanged.

The overrides exist so the test suite can point both roots at a temporary
directory. Without them a test run rewrites ``models/meta.json`` and appends
rows to ``data/projects.csv`` — the tracked training set — on every invocation.
"""
import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR_ENV = "SORA_DATA_DIR"
MODELS_DIR_ENV = "SORA_MODELS_DIR"


def data_dir() -> str:
    """Root for datasets and generated plots."""
    return os.environ.get(DATA_DIR_ENV) or os.path.join(ROOT_DIR, "data")


def models_dir() -> str:
    """Root for serialized models and their metadata."""
    return os.environ.get(MODELS_DIR_ENV) or os.path.join(ROOT_DIR, "models")


# ---------------------------------------------------------------- #191: seed, staged, active

SEED_DIR_ENV = "SORA_SEED_DIR"
RUNTIME_DIR_ENV = "SORA_RUNTIME_DIR"

#: How many staged candidates to keep. Enough to retry or compare a recent one,
#: few enough that a directory of pickles does not become the storage problem.
#: Chosen at 5 to start and expected to be tuned by artefact size and retrain
#: frequency, not by argument.
STAGED_RETENTION = int(os.environ.get("SORA_STAGED_RETENTION", "5"))


def seed_dir() -> str:
    """The immutable bootstrap. **`models/` is the seed directory** (#191).

    Decided 2026-08-16, after measuring what the alternative cost: relocating
    into `models/seed/` would have touched 37 hardcoded references across 18
    files, including training and calibration paths, for a change of name. The
    separation those decisions were after — an immutable bootstrap apart from a
    mutable runtime — is already achieved by `runtime/` being a separate named
    volume, not by the directory being called `seed`.

    So `models/` is the seed, and nothing at runtime writes to it. The recursive
    LFS pattern in `.gitattributes` protects any subdirectory added later; it is
    not a promise that one is coming.

    `models/seed/` is not a roadmap target unless a functional reason appears.
    """
    return os.environ.get(SEED_DIR_ENV) or models_dir()


def runtime_dir() -> str:
    """Where retraining writes. Outside the repository, and never `models/`.

    `models/` is the seed and is immutable at runtime: the only writer of
    anything under it is a commit. Inside the repository with a `.gitignore` is
    one typo away from committing a trained model — and on 2026-08-16 a probe
    overwrote six tracked files under `models/` in a single run, which is that
    failure without the typo.
    """
    return os.environ.get(RUNTIME_DIR_ENV) or os.path.join(ROOT_DIR, "runtime")


def staged_dir(run_id: str) -> str:
    """A candidate, under the id of the run that produced it.

    Keyed by `run_id` rather than a fixed name because the fixed names are the
    reason a past run's model cannot be recovered today: `model.pkl` is
    overwritten by the next retrain, so re-registering run N's model became
    impossible the moment run N+1 finished (#189).
    """
    # Validated as a UUID by app.model_source.validate_run_id, not merely
    # screened for separators: a screen would still admit ".", "~", a name
    # colliding with "active", or a control character.
    from app.model_source import validate_run_id

    return os.path.join(runtime_dir(), "staged", validate_run_id(run_id))


def active_dir() -> str:
    """The champion actually being served."""
    return os.path.join(runtime_dir(), "active")
