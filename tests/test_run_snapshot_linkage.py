from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

from app.data_snapshots import SnapshotSpec, publish_snapshot
from app.run_snapshots import (
    RunSnapshotError,
    decode_snapshot_ids,
    encode_snapshot_ids,
    resolve_training_snapshot_ids,
)


SHA = "a" * 40


def _linked_dataset(tmp_path):
    dataset = tmp_path / "projects.csv"
    dataset.write_bytes(b"budget,success\n10,1\n")
    spec = SnapshotSpec(
        source_ids=("world_bank_projects",),
        contract_versions={"world_bank_projects": "1.0"},
        fetch_time=datetime(2026, 10, 4, tzinfo=timezone.utc),
        as_of_time=datetime(2026, 10, 4, tzinfo=timezone.utc),
        parser_git_sha=SHA,
        request_parameters={},
        stage_counts={"written": 1},
        schema_fields=("budget", "success"),
        quality_checks={"valid": True},
    )
    root = tmp_path / "snapshots"
    published = publish_snapshot(root, spec, normalized=dataset.read_bytes())
    sidecar = {
        "snapshot_id": published.snapshot_id,
        "content_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
    }
    (tmp_path / "projects.csv.manifest.json").write_text(json.dumps(sidecar))
    return dataset, root, published.snapshot_id


def test_resolves_and_verifies_the_ordered_snapshot_ids(tmp_path):
    dataset, root, snapshot_id = _linked_dataset(tmp_path)
    assert resolve_training_snapshot_ids(dataset, snapshot_root=root) == (snapshot_id,)


def test_legacy_null_remains_explicitly_unknown():
    assert decode_snapshot_ids(None) is None


@pytest.mark.parametrize("value", [[], ["x" * 64], ["a" * 64, "a" * 64]])
def test_invalid_new_run_snapshot_sets_are_refused(value):
    with pytest.raises(RunSnapshotError):
        encode_snapshot_ids(value)


def test_missing_manifest_is_refused(tmp_path):
    dataset = tmp_path / "projects.csv"
    dataset.write_text("x\n1\n")
    with pytest.raises(RunSnapshotError, match="manifest is missing"):
        resolve_training_snapshot_ids(dataset, snapshot_root=tmp_path / "snapshots")


def test_snapshot_root_is_required(monkeypatch, tmp_path):
    dataset = tmp_path / "projects.csv"
    dataset.write_text("x\n1\n")
    monkeypatch.delenv("SORA_DATA_SNAPSHOT_ROOT", raising=False)
    with pytest.raises(RunSnapshotError, match="SORA_DATA_SNAPSHOT_ROOT is required"):
        resolve_training_snapshot_ids(dataset)


def test_changed_dataset_is_refused_before_training(tmp_path):
    dataset, root, _ = _linked_dataset(tmp_path)
    dataset.write_bytes(dataset.read_bytes() + b"11,0\n")
    with pytest.raises(RunSnapshotError, match="sidecar digest"):
        resolve_training_snapshot_ids(dataset, snapshot_root=root)


def test_tampered_snapshot_is_refused(tmp_path):
    dataset, root, snapshot_id = _linked_dataset(tmp_path)
    (root / snapshot_id / "normalized.bin").write_bytes(b"tampered")
    with pytest.raises(RunSnapshotError, match="verification failed"):
        resolve_training_snapshot_ids(dataset, snapshot_root=root)


@pytest.mark.parametrize("value", [[["a" * 64]], [{"snapshot_id": "a" * 64}], [None], [42]])
@pytest.mark.parametrize("entrypoint", ["encode", "decode", "resolve"])
def test_non_string_snapshot_ids_raise_domain_error(value, entrypoint, tmp_path):
    with pytest.raises(RunSnapshotError, match="64 lowercase hexadecimal"):
        if entrypoint == "encode":
            encode_snapshot_ids(value)
        elif entrypoint == "decode":
            decode_snapshot_ids(json.dumps(value))
        else:
            dataset, root, _ = _linked_dataset(tmp_path)
            sidecar = dataset.with_name(dataset.name + ".manifest.json")
            manifest = json.loads(sidecar.read_text())
            manifest.pop("snapshot_id")
            manifest["snapshot_ids"] = value
            sidecar.write_text(json.dumps(manifest))
            resolve_training_snapshot_ids(dataset, snapshot_root=root)
