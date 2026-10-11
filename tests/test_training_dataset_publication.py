"""Every mutable training-data writer must leave immutable provenance behind."""
from __future__ import annotations

import hashlib
import json

import pandas as pd
import pytest
from fastapi import HTTPException

from app.run_snapshots import resolve_training_dataset
from app.data_snapshots import load_snapshot


def _frame(rows: int = 12) -> pd.DataFrame:
    return pd.DataFrame({
        "budget": [1000.0] * rows,
        "co2_reduction": [10.0] * rows,
        "social_impact": [5.0] * rows,
        "duration_months": [12.0] * rows,
        "success": [index % 2 for index in range(rows)],
    })


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    import app.api.retrain as retrain

    path = tmp_path / "projects.csv"
    _frame().to_csv(path, index=False)
    models = tmp_path / "models"
    models.mkdir()
    for name in ("model.pkl", "scaler.pkl", "best_threshold.pkl"):
        (models / name).write_bytes(b"seed-" + name.encode())
    (models / "meta.json").write_text(json.dumps({"retrained_at": "20260101_000000"}))
    (models / "metrics.json").write_text(json.dumps({"roc_auc": 0.5}))
    monkeypatch.setattr(retrain, "PROJECTS_CSV", str(path))
    monkeypatch.setattr(retrain, "MODELS_DIR", str(models))
    monkeypatch.setattr(retrain, "PRED_LOG", str(tmp_path / "absent.csv"))
    monkeypatch.setattr(retrain, "DATASET_LOCK", str(tmp_path / ".projects.lock"))
    monkeypatch.setenv("SORA_MODELS_DIR", str(models))
    monkeypatch.setenv("SORA_SEED_DIR", str(models))
    monkeypatch.setenv("SORA_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("SORA_DATA_SNAPSHOT_ROOT", str(tmp_path / "snapshots"))
    monkeypatch.setenv("SORA_GIT_SHA", "a" * 40)
    return path


def _assert_current_pair(path, snapshot_root):
    assert path.is_symlink()
    resolved = resolve_training_dataset(path, snapshot_root=snapshot_root)
    assert resolved.content == path.read_bytes()
    return resolved.snapshot_ids[0]


def test_bulk_upload_shared_writer_publishes_a_verifiable_pair(dataset, monkeypatch):
    import app.api.retrain as retrain

    events = []
    result = retrain._ingest_frame(
        _frame(2), auto_retrain=False,
        _audit=lambda outcome, **details: events.append((outcome, details)),
    )

    snapshot_id = _assert_current_pair(dataset, dataset.parent / "snapshots")
    assert result["rows_added"] == 2
    assert events == [("uploaded", {"rows": 2})]
    assert (dataset.parent / "snapshots" / snapshot_id / "normalized.bin").exists()


def test_refresh_writer_publishes_the_same_verifiable_pair(dataset, monkeypatch):
    import app.api.retrain as retrain

    class _Query:
        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def first(self):
            return None

    class _Session:
        def query(self, *_args):
            return _Query()

        def close(self):
            pass

    monkeypatch.setattr("app.database.SessionLocal", lambda: _Session())
    result = retrain.data_refresh(
        budget=2000.0, co2_reduction=11.0, social_impact=6.0,
        duration_months=13, success=1, auto_retrain_threshold=9999,
        current_user=None,
    )

    _assert_current_pair(dataset, dataset.parent / "snapshots")
    assert result["status"] == "added"
    assert result["total_samples"] == 13


def test_snapshot_failure_leaves_the_previous_csv_and_sidecar_untouched(dataset, monkeypatch):
    import app.api.retrain as retrain

    # First publication supplies a coherent previous state to protect.
    retrain._ingest_frame(_frame(1), auto_retrain=False, _audit=lambda *_args, **_kwargs: None)
    sidecar = dataset.resolve().parent / "manifest.json"
    before_csv = dataset.read_bytes()
    before_sidecar = sidecar.read_bytes()

    def fail_publish(*_args, **_kwargs):
        raise RuntimeError("snapshot store unavailable")

    monkeypatch.setattr("app.training_dataset.publish_training_dataset", fail_publish)
    with pytest.raises(HTTPException, match="dataset; it is unchanged"):
        retrain._ingest_frame(_frame(1), auto_retrain=False, _audit=lambda *_args, **_kwargs: None)

    assert dataset.read_bytes() == before_csv
    assert sidecar.read_bytes() == before_sidecar
    _assert_current_pair(dataset, dataset.parent / "snapshots")


def test_training_accepts_the_snapshot_published_by_the_shared_writer(dataset):
    import app.api.retrain as retrain

    retrain._ingest_frame(_frame(2), auto_retrain=False, _audit=lambda *_args, **_kwargs: None)
    result = retrain._do_retrain(min_samples=10, trigger_source="publication_regression")

    assert result["data_version"] == "sha256:" + hashlib.sha256(dataset.read_bytes()).hexdigest()
    assert result["snapshot_ids"] == [
        _assert_current_pair(dataset, dataset.parent / "snapshots")
    ]


def test_the_only_runtime_writers_use_the_common_publication_primitive():
    import inspect
    import app.api.retrain as retrain

    assert "_replace_projects_csv(df_new)" in inspect.getsource(retrain.data_refresh)
    assert "_replace_projects_csv(df_merged)" in inspect.getsource(retrain._ingest_frame)
    assert "to_csv(PROJECTS_CSV" not in inspect.getsource(retrain)


def _append(frame=None, *, auto_retrain=False, audit=None):
    from app.api.retrain import _ingest_frame
    return _ingest_frame(
        _frame(1) if frame is None else frame, auto_retrain,
        audit or (lambda *_args, **_kwargs: None),
    )


def test_unknown_legacy_bytes_remain_available_and_are_not_claimed_as_operator_input(dataset):
    before = dataset.read_bytes()
    _append()
    root = dataset.parent / "snapshots"
    first = load_snapshot(root, _assert_current_pair(dataset, root))
    assert (first.path / "raw.bin").read_bytes() == before
    assert first.manifest["quality_checks"]["legacy_origin"].startswith("unknown")
    submission = load_snapshot(root, first.manifest["quality_checks"]["submission_snapshot_id"])
    assert len(pd.read_csv(submission.path / "normalized.bin")) == 1
    _append()
    second = load_snapshot(root, _assert_current_pair(dataset, root))
    assert second.manifest["quality_checks"]["parent_snapshot_id"] == first.snapshot_id
    assert second.manifest["quality_checks"]["legacy_origin"] == first.manifest["quality_checks"]["legacy_origin"]
    assert (first.path / "raw.bin").read_bytes() == before


def test_known_parent_provenance_and_legacy_sidecar_survive(dataset):
    from datetime import datetime, timezone
    from app.data_snapshots import SnapshotSpec, publish_snapshot
    root = dataset.parent / "snapshots"
    now = datetime.now(timezone.utc)
    parent = publish_snapshot(root, SnapshotSpec(
        source_ids=("world_bank_projects",), contract_versions={"world_bank_projects": "1.0"},
        fetch_time=now, as_of_time=now, parser_git_sha="a" * 40,
        request_parameters={}, stage_counts={}, schema_fields=tuple(_frame().columns), quality_checks={},
    ), normalized=dataset.read_bytes())
    sidecar = dataset.with_name(dataset.name + ".manifest.json")
    sidecar.write_text(json.dumps({"snapshot_id": parent.snapshot_id,
                                  "content_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest()}))
    before = sidecar.read_bytes()
    _append()
    published = load_snapshot(root, _assert_current_pair(dataset, root))
    assert published.manifest["source_ids"] == ["operator_training_data", "world_bank_projects"]
    assert published.manifest["contract_versions"] == {"operator_training_data": "1.0", "world_bank_projects": "1.0"}
    assert published.manifest["quality_checks"]["parent_snapshot_id"] == parent.snapshot_id
    assert sidecar.read_bytes() == before


def test_multiline_csv_count_is_rows_not_newlines(dataset):
    frame = _frame(1)
    frame["name"] = "first line\nsecond line"
    _append(frame)
    root = dataset.parent / "snapshots"
    published = load_snapshot(root, _assert_current_pair(dataset, root))
    assert published.manifest["stage_counts"]["total_written"] == 13
    assert len(pd.read_csv(dataset)) == 13


@pytest.mark.parametrize("failure", ["symlink", "before_rename", "rename"])
def test_failure_before_pointer_commit_preserves_previous_version(dataset, monkeypatch, failure):
    import errno
    import app.training_dataset as publication
    _append()
    previous = resolve_training_dataset(dataset)
    def fail(*_args, **_kwargs):
        raise OSError(errno.EIO, "injected disk failure")
    if failure == "symlink":
        monkeypatch.setattr(publication.Path, "symlink_to", fail)
    elif failure == "before_rename":
        monkeypatch.setattr(publication, "_fsync_directory", fail)
    else:
        original = publication.os.replace
        def fail_pointer(source, destination):
            if destination == dataset:
                fail()
            return original(source, destination)
        monkeypatch.setattr(publication.os, "replace", fail_pointer)
    with pytest.raises(HTTPException, match="unchanged"):
        _append()
    assert resolve_training_dataset(dataset) == previous
    assert list(dataset.parent.glob(".projects-pointer-*")) == []


def test_failure_after_pointer_commit_reports_uncertain_and_does_not_retrain(dataset, monkeypatch):
    import errno
    import app.training_dataset as publication
    import app.api.retrain as retrain
    _append()
    previous = resolve_training_dataset(dataset)
    original = publication._fsync_directory
    calls = 0
    def fsync(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(errno.EIO, "injected directory failure")
        return original(path)
    def no_train(*_args, **_kwargs):
        pytest.fail("retrain after an uncertain commit")
    monkeypatch.setattr(publication, "_fsync_directory", fsync)
    monkeypatch.setattr(retrain, "_do_retrain", no_train)
    events = []
    with pytest.raises(HTTPException) as caught:
        _append(auto_retrain=True, audit=lambda event, **_kwargs: events.append(event))
    assert caught.value.status_code == 503
    assert "unconfirmed" in caught.value.detail
    assert "unchanged" not in caught.value.detail
    assert resolve_training_dataset(dataset).snapshot_ids != previous.snapshot_ids
    assert len(pd.read_csv(dataset)) == 14
    assert events == ["commit_uncertain"]


def test_reader_uses_one_version_when_pointer_changes_during_verification(dataset, monkeypatch):
    import app.run_snapshots as reader
    from app.training_dataset import activate_training_snapshot
    _append()
    first = resolve_training_dataset(dataset)
    root = dataset.parent / "snapshots"
    old_snapshot = load_snapshot(root, first.snapshot_ids[0])
    _append()
    new_snapshot = load_snapshot(root, resolve_training_dataset(dataset).snapshot_ids[0])
    activate_training_snapshot(dataset, old_snapshot)
    original = reader.load_snapshot
    def switch_then_read(store, snapshot_id):
        activate_training_snapshot(dataset, new_snapshot)
        return original(store, snapshot_id)
    monkeypatch.setattr(reader, "load_snapshot", switch_then_read)
    assert resolve_training_dataset(dataset) == first
    assert dataset.resolve().parent.name == new_snapshot.snapshot_id


@pytest.mark.parametrize("sha", [None, "not-a-sha", " ", "abcdef0"])
def test_production_refuses_missing_or_invalid_sha_before_publication(dataset, monkeypatch, sha):
    import app.training_dataset as publication
    monkeypatch.setenv("SORA_ENV", "production")
    if sha is None:
        monkeypatch.delenv("SORA_GIT_SHA")
    else:
        monkeypatch.setenv("SORA_GIT_SHA", sha)
    def forbidden(*_args, **_kwargs):
        pytest.fail("production must not discover Git or publish without its build SHA")
    monkeypatch.setattr(publication.subprocess, "run", forbidden)
    monkeypatch.setattr(publication, "publish_snapshot", forbidden)
    before = dataset.read_bytes()
    with pytest.raises(HTTPException, match="unchanged"):
        _append()
    assert dataset.read_bytes() == before
    assert not dataset.is_symlink()


def test_production_uses_baked_sha_without_git(dataset, monkeypatch):
    import app.training_dataset as publication
    monkeypatch.setenv("SORA_ENV", "production")
    monkeypatch.setenv("SORA_GIT_SHA", "b" * 40)
    def forbidden(*_args, **_kwargs):
        pytest.fail("production must not execute Git")
    monkeypatch.setattr(publication.subprocess, "run", forbidden)
    _append()
    root = dataset.parent / "snapshots"
    published = load_snapshot(root, _assert_current_pair(dataset, root))
    assert published.manifest["parser_git_sha"] == "b" * 40


@pytest.mark.parametrize("script,option", [
    ("enrich_worldbank_dataset", "--output"), ("fetch_wb_projects", "--out"),
    ("enrich_worldbank_dataset", "--raw-output"),
])
@pytest.mark.parametrize("alias", ["direct", "symlink", "hardlink", "sidecar", "snapshot"])
def test_offline_builders_refuse_active_paths_before_network(dataset, monkeypatch, script, option, alias):
    import importlib
    import os
    from app.training_dataset import TrainingDatasetPublicationError
    monkeypatch.setenv("SORA_DATA_DIR", str(dataset.parent))
    output = dataset
    if alias in {"symlink", "hardlink"}:
        output = dataset.with_name("alias.csv")
        if alias == "symlink":
            output.symlink_to(dataset)
        else:
            os.link(dataset, output)
    elif alias == "sidecar":
        output = dataset.with_name(dataset.name + ".manifest.json")
    elif alias == "snapshot":
        _append()
        output = dataset.resolve()
    module = importlib.import_module("scripts." + script)
    monkeypatch.setattr("sys.argv", [script, option, str(output)])
    def no_network(*_args, **_kwargs):
        pytest.fail("guard must run before network")
    monkeypatch.setattr("requests.sessions.Session.request", no_network)
    before = dataset.read_bytes()
    with pytest.raises(TrainingDatasetPublicationError, match="cannot overwrite"):
        module.main()
    assert dataset.read_bytes() == before


def test_operator_contract_requires_admin():
    from app.ingesters.data_contracts import source_contract
    assert source_contract("operator_training_data")["access"]["authentication"] == "required_admin"


@pytest.mark.parametrize("phase", ["before", "after"])
def test_process_exit_at_commit_boundary_keeps_a_complete_version(dataset, phase):
    import subprocess
    import sys
    from app.training_dataset import activate_training_snapshot
    _append()
    old = resolve_training_dataset(dataset)
    root = dataset.parent / "snapshots"
    _append()
    new = resolve_training_dataset(dataset)
    activate_training_snapshot(dataset, load_snapshot(root, old.snapshot_ids[0]))
    code = """
import os, sys
from pathlib import Path
from app.data_snapshots import load_snapshot
from app.training_dataset import activate_training_snapshot
dataset, root, snapshot_id, phase = sys.argv[1:]
replace = os.replace
def crash(source, destination):
    if phase == 'after':
        replace(source, destination)
    os._exit(97)
os.replace = crash
activate_training_snapshot(Path(dataset), load_snapshot(root, snapshot_id))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(dataset), str(root), new.snapshot_ids[0], phase],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 97, result.stderr
    assert resolve_training_dataset(dataset) == {"before": old, "after": new}[phase]


def test_another_process_holds_the_lock_before_read_and_retry_keeps_both_writes(dataset):
    import subprocess
    import sys
    from app.api.retrain import DATASET_LOCK
    code = """
import fcntl, sys
with open(sys.argv[1], 'a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    print('locked', flush=True)
    sys.stdin.readline()
"""
    with subprocess.Popen([sys.executable, "-c", code, DATASET_LOCK],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as holder:
        try:
            assert holder.stdout.readline().strip() == "locked"
            with pytest.raises(HTTPException) as caught:
                _append()
            assert caught.value.status_code == 409
            assert len(pd.read_csv(dataset)) == 12
        finally:
            holder.communicate("release\n", timeout=10)
        assert holder.returncode == 0
    _append()
    first = resolve_training_dataset(dataset).snapshot_ids[0]
    _append()
    root = dataset.parent / "snapshots"
    second = load_snapshot(root, _assert_current_pair(dataset, root))
    assert second.manifest["quality_checks"]["parent_snapshot_id"] == first
    assert len(pd.read_csv(dataset)) == 14
