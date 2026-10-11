"""Immutable provenance records for administrator-mutated training datasets."""
from __future__ import annotations

import contextlib
import hashlib
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.data_snapshots import (
    PublishedSnapshot, SnapshotSpec, _fsync_directory, load_snapshot, publish_snapshot,
)
from app.ingesters.data_contracts import source_contract


SOURCE_ID = "operator_training_data"


class TrainingDatasetPublicationError(RuntimeError):
    """The mutable CSV must not change without immutable provenance."""


def _parser_git_sha() -> str:
    """Name the code that serialized the dataset or refuse publication."""
    configured = os.getenv("SORA_GIT_SHA")
    if os.getenv("SORA_ENV") == "production" and not configured:
        raise TrainingDatasetPublicationError("SORA_GIT_SHA is required in production")
    if configured:
        value = configured.strip()
    else:
        try:
            value = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
                cwd=Path(__file__).resolve().parents[1],
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise TrainingDatasetPublicationError(
                "SORA_GIT_SHA is required when the application source is not a Git checkout"
            ) from exc
    if len(value) < 7 or len(value) > 64 or any(char not in "0123456789abcdef" for char in value):
        raise TrainingDatasetPublicationError("SORA_GIT_SHA must be a 7-64 character lowercase Git SHA")
    if os.getenv("SORA_ENV") == "production" and len(value) not in (40, 64):
        raise TrainingDatasetPublicationError("SORA_GIT_SHA must be a full Git SHA in production")
    return value


def publish_training_dataset(
    content: bytes, *, schema_fields: tuple[str, ...], row_count: int,
    previous_content: bytes, submitted_content: bytes,
    parent_snapshot_id: str | None,
) -> PublishedSnapshot:
    """Publish the delta and composite before switching the active pointer."""
    root_value = os.getenv("SORA_DATA_SNAPSHOT_ROOT")
    if not root_value:
        raise TrainingDatasetPublicationError(
            "SORA_DATA_SNAPSHOT_ROOT is required before changing the training dataset"
        )
    if not content:
        raise TrainingDatasetPublicationError("training dataset must not be empty")

    now = datetime.now(timezone.utc)
    contract = source_contract(SOURCE_ID)
    sha = _parser_git_sha()
    versions = {SOURCE_ID: contract["contract_version"]}
    lineage = {"parent_snapshot_id": parent_snapshot_id}
    if parent_snapshot_id is not None:
        parent = load_snapshot(root_value, parent_snapshot_id)
        if parent.manifest["normalized"]["sha256"] != hashlib.sha256(previous_content).hexdigest():
            raise TrainingDatasetPublicationError("parent snapshot does not match the previous dataset")
        parent_versions = parent.manifest["contract_versions"]
        if SOURCE_ID in parent_versions and parent_versions[SOURCE_ID] != versions[SOURCE_ID]:
            raise TrainingDatasetPublicationError("operator contract changed; explicit migration required")
        versions.update(parent_versions)
        lineage["legacy_origin"] = parent.manifest["quality_checks"].get("legacy_origin")
    else:
        lineage["legacy_origin"] = "unknown; previous bytes retained as raw.bin"
    submitted = publish_snapshot(
        root_value,
        SnapshotSpec(
            source_ids=(SOURCE_ID,), contract_versions={SOURCE_ID: contract["contract_version"]},
            fetch_time=now, as_of_time=now, parser_git_sha=sha,
            request_parameters={}, stage_counts={}, schema_fields=schema_fields,
            quality_checks={"role": "operator submission"},
        ), normalized=submitted_content,
    )
    lineage["submission_snapshot_id"] = submitted.snapshot_id
    return publish_snapshot(
        Path(root_value),
        SnapshotSpec(
            source_ids=tuple(sorted(versions)),
            contract_versions=versions,
            fetch_time=now,
            as_of_time=now,
            parser_git_sha=sha,
            request_parameters={},
            stage_counts={"total_written": row_count},
            schema_fields=schema_fields,
            quality_checks={
                "content_sha256": hashlib.sha256(content).hexdigest(),
                "publication": "composite: previous dataset plus operator submission",
                **lineage,
            },
        ),
        normalized=content,
        raw=previous_content if parent_snapshot_id is None else None,
    )


class DatasetCommitUncertain(TrainingDatasetPublicationError):
    """The new complete version is visible but its durability is unconfirmed."""


def activate_training_snapshot(dataset: Path, published: PublishedSnapshot) -> None:
    """One rename selects CSV and manifest together; never rewrite a snapshot.

    A legacy CSV is retained verbatim in the first composite's raw.bin. Any
    legacy sidecar remains untouched for audit; readers of a pointer use the
    immutable manifest beside its target instead. No pair of mutable files is
    claimed to be a filesystem transaction.
    """
    temporary = dataset.with_name(f".projects-pointer-{uuid.uuid4().hex}")
    try:
        target = published.path.resolve() / "normalized.bin"
        temporary.symlink_to(os.path.relpath(target, dataset.parent.resolve()))
        _fsync_directory(dataset.parent)
        os.replace(temporary, dataset)
        try:
            _fsync_directory(dataset.parent)
        except OSError as exc:
            raise DatasetCommitUncertain(
                "Dataset replacement is visible; durability is unconfirmed. Inspect before retrying."
            ) from exc
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def refuse_active_dataset_output(output: str, *, snapshot_root: str | None = None) -> None:
    """Offline builders must not overwrite the active dataset or its store."""
    from app.paths import data_dir

    candidate = Path(output).resolve()
    canonical = Path(data_dir()) / "projects.csv"
    repository_csv = Path(__file__).resolve().parents[1] / "data" / "projects.csv"
    protected = (canonical, repository_csv,
                 Path(str(canonical) + ".manifest.json"),
                 Path(str(repository_csv) + ".manifest.json"))
    stores = {value for value in (snapshot_root, os.getenv("SORA_DATA_SNAPSHOT_ROOT")) if value}
    if (any(candidate == path.resolve() or (
                candidate.exists() and path.exists() and candidate.samefile(path)
            ) for path in protected)
            or any(candidate.is_relative_to(Path(store).resolve()) for store in stores)):
        raise TrainingDatasetPublicationError(
            "Offline builders cannot overwrite the active training dataset or snapshot store; "
            "choose a separate output path."
        )
