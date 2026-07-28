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
