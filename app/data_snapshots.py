"""Content-addressed, immutable dataset snapshots.

This module owns local snapshot publication semantics only.  It does not choose
the production storage backend and it does not connect existing training paths;
those are separate rollout decisions.  The local implementation gives every
backend a contract to preserve: deterministic identity, redacted metadata,
atomic publication, durable writes, and fail-closed verification.
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from app.ingesters.data_contracts import source_contract


_SECRET_KEY = re.compile(r"(?i)(token|password|secret|api[_-]?key|authorization)")
_SHA = re.compile(r"^[0-9a-f]{7,64}$")
_SNAPSHOT_ID = re.compile(r"^[0-9a-f]{64}$")


class SnapshotError(ValueError):
    """Snapshot input, persisted bytes, or immutable state is invalid."""


@dataclass(frozen=True)
class SnapshotSpec:
    source_ids: tuple[str, ...]
    contract_versions: Mapping[str, str]
    fetch_time: datetime
    as_of_time: datetime
    parser_git_sha: str
    request_parameters: Mapping[str, Any]
    stage_counts: Mapping[str, int]
    schema_fields: tuple[str, ...]
    quality_checks: Mapping[str, Any]
    exclusions: tuple[str, ...] = ()
    event_time_start: datetime | None = None
    event_time_end: datetime | None = None


@dataclass(frozen=True)
class PublishedSnapshot:
    snapshot_id: str
    path: Path
    manifest: Mapping[str, Any]


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc(value: datetime | None, field: str) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise SnapshotError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[redacted]" if _SECRET_KEY.search(str(key)) else _redact(child)
            for key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise SnapshotError("request parameters must not contain NaN or Infinity")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise SnapshotError(f"request parameter type is not JSON-safe: {type(value).__name__}")


def _manifest_core(spec: SnapshotSpec, raw: bytes | None, normalized: bytes) -> dict[str, Any]:
    if not spec.source_ids or len(set(spec.source_ids)) != len(spec.source_ids):
        raise SnapshotError("source_ids must be non-empty and unique")
    source_ids = sorted(spec.source_ids)
    if set(spec.contract_versions) != set(source_ids):
        raise SnapshotError("contract_versions must exactly match source_ids")
    for source_id in source_ids:
        contract = source_contract(source_id)
        if spec.contract_versions[source_id] != contract["contract_version"]:
            raise SnapshotError(
                f"{source_id} contract version {spec.contract_versions[source_id]!r} "
                f"does not match {contract['contract_version']!r}"
            )
    if not _SHA.fullmatch(spec.parser_git_sha):
        raise SnapshotError("parser_git_sha must be 7-64 lowercase hexadecimal characters")
    if not spec.schema_fields or len(set(spec.schema_fields)) != len(spec.schema_fields):
        raise SnapshotError("schema_fields must be non-empty and unique")
    for stage, count in spec.stage_counts.items():
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise SnapshotError(f"stage count {stage!r} must be a non-negative integer")

    start = _utc(spec.event_time_start, "event_time_start")
    end = _utc(spec.event_time_end, "event_time_end")
    if start is not None and end is not None and start > end:
        raise SnapshotError("event_time_start must not be after event_time_end")

    schema = list(spec.schema_fields)
    return {
        "manifest_version": "1.0",
        "source_ids": source_ids,
        "contract_versions": dict(spec.contract_versions),
        "request_parameters": _redact(spec.request_parameters),
        "fetch_time": _utc(spec.fetch_time, "fetch_time"),
        "as_of_time": _utc(spec.as_of_time, "as_of_time"),
        "event_time_start": start,
        "event_time_end": end,
        "parser_git_sha": spec.parser_git_sha,
        "stage_counts": dict(spec.stage_counts),
        "schema_fields": schema,
        "schema_sha256": _digest(_canonical_bytes(schema)),
        "quality_checks": _redact(spec.quality_checks),
        "exclusions": sorted(spec.exclusions),
        "raw": None if raw is None else {"file": "raw.bin", "sha256": _digest(raw), "bytes": len(raw)},
        "normalized": {"file": "normalized.bin", "sha256": _digest(normalized), "bytes": len(normalized)},
    }


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError as exc:
        if exc.errno in {errno.EINVAL, errno.ENOTSUP}:
            return
        raise
    try:
        try:
            os.fsync(descriptor)
        except OSError as exc:
            if exc.errno not in {errno.EINVAL, errno.ENOTSUP}:
                raise
    finally:
        os.close(descriptor)


def _write_durable(path: Path, data: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


@contextmanager
def _store_lock(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".snapshot.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def publish_snapshot(
    root: Path | str,
    spec: SnapshotSpec,
    *,
    normalized: bytes,
    raw: bytes | None = None,
) -> PublishedSnapshot:
    """Atomically publish or verify one immutable local snapshot."""
    if not isinstance(normalized, bytes):
        raise SnapshotError("normalized payload must be bytes")
    if raw is not None and not isinstance(raw, bytes):
        raise SnapshotError("raw payload must be bytes or None")

    core = _manifest_core(spec, raw, normalized)
    snapshot_id = _digest(_canonical_bytes(core))
    manifest = {
        **core,
        "snapshot_id": snapshot_id,
        "storage_locator": f"snapshot:{snapshot_id}",
    }
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    ).encode("utf-8") + b"\n"

    root_path = Path(root)
    target = root_path / snapshot_id
    with _store_lock(root_path):
        if target.exists():
            existing = load_snapshot(root_path, snapshot_id)
            if existing.manifest != manifest:
                raise SnapshotError(f"snapshot {snapshot_id} already exists with different manifest")
            return existing

        temporary = Path(tempfile.mkdtemp(prefix=".snapshot-", dir=root_path))
        try:
            if raw is not None:
                _write_durable(temporary / "raw.bin", raw)
            _write_durable(temporary / "normalized.bin", normalized)
            _write_durable(temporary / "manifest.json", manifest_bytes)
            _fsync_directory(temporary)
            os.replace(temporary, target)
            _fsync_directory(root_path)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    return load_snapshot(root_path, snapshot_id)


def load_snapshot(root: Path | str, snapshot_id: str) -> PublishedSnapshot:
    """Load and cryptographically verify one published snapshot."""
    if not _SNAPSHOT_ID.fullmatch(snapshot_id):
        raise SnapshotError("snapshot_id must be 64 lowercase hexadecimal characters")
    path = Path(root) / snapshot_id
    try:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"cannot read snapshot {snapshot_id}: {exc}") from exc
    if manifest.get("snapshot_id") != snapshot_id:
        raise SnapshotError(f"snapshot directory {snapshot_id} disagrees with manifest id")
    if manifest.get("storage_locator") != f"snapshot:{snapshot_id}":
        raise SnapshotError(f"snapshot {snapshot_id} has an invalid logical locator")

    core = dict(manifest)
    core.pop("snapshot_id", None)
    core.pop("storage_locator", None)
    if _digest(_canonical_bytes(core)) != snapshot_id:
        raise SnapshotError(f"snapshot {snapshot_id} manifest digest mismatch")

    for key in ("raw", "normalized"):
        descriptor = manifest.get(key)
        if descriptor is None:
            continue
        file_path = path / descriptor["file"]
        try:
            payload = file_path.read_bytes()
        except OSError as exc:
            raise SnapshotError(f"snapshot {snapshot_id} cannot read {key}: {exc}") from exc
        if len(payload) != descriptor["bytes"] or _digest(payload) != descriptor["sha256"]:
            raise SnapshotError(f"snapshot {snapshot_id} {key} payload digest mismatch")

    return PublishedSnapshot(snapshot_id=snapshot_id, path=path, manifest=manifest)
