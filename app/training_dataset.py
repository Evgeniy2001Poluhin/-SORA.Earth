"""Immutable provenance records for administrator-mutated training datasets."""
from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from app.data_snapshots import PublishedSnapshot, SnapshotSpec, publish_snapshot
from app.ingesters.data_contracts import source_contract


SOURCE_ID = "operator_training_data"


class TrainingDatasetPublicationError(RuntimeError):
    """The mutable CSV must not change without immutable provenance."""


def _parser_git_sha() -> str:
    """Name the code that serialized the dataset or refuse publication."""
    configured = os.getenv("SORA_GIT_SHA")
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
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise TrainingDatasetPublicationError(
                "SORA_GIT_SHA is required when the application source is not a Git checkout"
            ) from exc
    if len(value) < 7 or len(value) > 64 or any(char not in "0123456789abcdef" for char in value):
        raise TrainingDatasetPublicationError("SORA_GIT_SHA must be a 7-64 character lowercase Git SHA")
    return value


def publish_training_dataset(content: bytes, *, schema_fields: tuple[str, ...]) -> PublishedSnapshot:
    """Publish immutable bytes before their mutable compatibility copy exists."""
    root_value = os.getenv("SORA_DATA_SNAPSHOT_ROOT")
    if not root_value:
        raise TrainingDatasetPublicationError(
            "SORA_DATA_SNAPSHOT_ROOT is required before changing the training dataset"
        )
    if not content:
        raise TrainingDatasetPublicationError("training dataset must not be empty")

    now = datetime.now(timezone.utc)
    contract = source_contract(SOURCE_ID)
    return publish_snapshot(
        Path(root_value),
        SnapshotSpec(
            source_ids=(SOURCE_ID,),
            contract_versions={SOURCE_ID: contract["contract_version"]},
            fetch_time=now,
            as_of_time=now,
            parser_git_sha=_parser_git_sha(),
            request_parameters={},
            stage_counts={"total_written": content.count(b"\n") - 1},
            schema_fields=schema_fields,
            quality_checks={
                "content_sha256": hashlib.sha256(content).hexdigest(),
                "publication": "administrator training-data mutation",
            },
        ),
        normalized=content,
    )
