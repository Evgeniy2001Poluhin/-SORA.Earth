"""Every mutable training-data writer must leave immutable provenance behind."""
from __future__ import annotations

import hashlib
import json

import pandas as pd
import pytest
from fastapi import HTTPException

from app.run_snapshots import resolve_training_dataset


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
    manifest = json.loads((path.parent / (path.name + ".manifest.json")).read_text())
    assert manifest["snapshot_ids"] and len(manifest["snapshot_ids"]) == 1
    assert manifest["content_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    resolved = resolve_training_dataset(path, snapshot_root=snapshot_root)
    assert resolved.content == path.read_bytes()
    return manifest["snapshot_ids"][0]


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
    sidecar = dataset.parent / (dataset.name + ".manifest.json")
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
