"""Resolve immutable dataset evidence before a model run starts."""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from app.data_snapshots import SnapshotError, load_snapshot


_SNAPSHOT_ID = re.compile(r"^[0-9a-f]{64}$")


class RunSnapshotError(ValueError):
    """A new run cannot prove which immutable dataset snapshot it used."""


@dataclass(frozen=True)
class ResolvedTrainingDataset:
    """Verified lineage and the exact bytes a run is allowed to read."""

    snapshot_ids: tuple[str, ...]
    content: bytes

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


def encode_snapshot_ids(snapshot_ids: Iterable[str]) -> str:
    values = list(snapshot_ids)
    if not values:
        raise RunSnapshotError("new runs require at least one dataset snapshot")
    if len(values) != len(set(values)):
        raise RunSnapshotError("dataset snapshot ids must be unique and ordered")
    if any(not isinstance(value, str) or not _SNAPSHOT_ID.fullmatch(value) for value in values):
        raise RunSnapshotError("dataset snapshot ids must be 64 lowercase hexadecimal characters")
    return json.dumps(values, separators=(",", ":"))


def decode_snapshot_ids(value: str | None) -> tuple[str, ...] | None:
    """Return None for a legacy unknown; reject malformed recorded evidence."""
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RunSnapshotError("recorded dataset snapshot ids are not valid JSON") from exc
    if not isinstance(parsed, list):
        raise RunSnapshotError("recorded dataset snapshot ids must be an ordered array")
    encode_snapshot_ids(parsed)
    return tuple(parsed)


def resolve_training_dataset(
    dataset_path: str | Path,
    *,
    snapshot_root: str | Path | None = None,
) -> ResolvedTrainingDataset:
    """Resolve lineage and capture the verified bytes exactly once."""
    dataset = Path(dataset_path)
    root_value = snapshot_root or os.getenv("SORA_DATA_SNAPSHOT_ROOT")
    if not root_value:
        raise RunSnapshotError("SORA_DATA_SNAPSHOT_ROOT is required for new training runs")
    root = Path(root_value)
    sidecar = Path(str(dataset) + ".manifest.json")
    try:
        manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RunSnapshotError(f"training dataset manifest is missing: {sidecar.name}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RunSnapshotError(f"training dataset manifest is unreadable: {type(exc).__name__}") from exc
    if not isinstance(manifest, dict):
        raise RunSnapshotError("training dataset manifest must be a JSON object")

    values = manifest.get("snapshot_ids")
    if values is None and manifest.get("snapshot_id") is not None:
        values = [manifest["snapshot_id"]]
    encoded = encode_snapshot_ids(values or [])
    snapshot_ids = decode_snapshot_ids(encoded)
    assert snapshot_ids is not None

    try:
        content = dataset.read_bytes()
    except OSError as exc:
        raise RunSnapshotError(f"training dataset is unreadable: {type(exc).__name__}") from exc
    actual = hashlib.sha256(content).hexdigest()
    claimed = manifest.get("content_sha256")
    if claimed != actual:
        raise RunSnapshotError("training dataset bytes disagree with the sidecar digest")

    try:
        loaded = [load_snapshot(root, snapshot_id) for snapshot_id in snapshot_ids]
    except SnapshotError as exc:
        raise RunSnapshotError(f"training dataset snapshot verification failed: {exc}") from exc
    if len(loaded) == 1 and loaded[0].manifest["normalized"]["sha256"] != actual:
        raise RunSnapshotError("training dataset bytes disagree with the immutable snapshot")
    return ResolvedTrainingDataset(snapshot_ids=snapshot_ids, content=content)


def resolve_training_snapshot_ids(
    dataset_path: str | Path,
    *,
    snapshot_root: str | Path | None = None,
) -> tuple[str, ...]:
    """Compatibility helper for callers that only need the ordered ids."""
    return resolve_training_dataset(
        dataset_path, snapshot_root=snapshot_root
    ).snapshot_ids
