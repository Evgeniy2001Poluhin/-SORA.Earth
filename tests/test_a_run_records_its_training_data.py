"""A retrain records which data it trained on.

`docs/DEVELOPMENT_ROADMAP.md` defines the project as ready when a specialist can
follow an output back along the whole chain, and `data snapshot → run_id` is one
of its links. Measured 2026-09-20, that link was empty:

    retrain_log.data_version    declared String(100), and not one of the eight
                                _finish_retrain_log call sites passed it, so
                                every row carried NULL
    meta.json                   retrained_at, algorithm, n_estimators,
                                max_depth, features, total_samples -- how many
                                rows, never which

The data is not static. `POST /model/data/bulk-upload/content` replaces
`data/projects.csv` atomically, and `_do_retrain`'s own comment records that the
file "is appended to by more than the validated upload path". So two champions
trained a week apart differed in their training set with nothing in the record
able to say how.

The file digest distinguishes byte sets, but it cannot reproduce either one.
New runs therefore resolve a cryptographically verified immutable snapshot
before reading the mutable training path and repeat its id in the journal and
candidate metadata. Legacy rows remain explicitly unknown.

**The timeout marker is deliberate.** Every test here fits a RandomForest, and
one of them fits two. `pytest.ini` gives 30 s; `tests/test_training_does_not_promote.py`
has no marker and its two-retrain case is one of the suite's intermittent
failures. Dropping this marker restores that.
"""
import hashlib
import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from app.paths import staged_dir
from app.data_snapshots import SnapshotSpec, publish_snapshot
from app.run_snapshots import RunSnapshotError

pytestmark = pytest.mark.timeout(120)


def _frame(rows=400, seed=5):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "budget": rng.uniform(1000, 900000, rows),
        "co2_reduction": rng.uniform(1, 9000, rows),
        "social_impact": rng.uniform(1, 99, rows),
        "duration_months": rng.integers(1, 119, rows),
        "success": ([1] * (rows // 5)) + ([0] * (rows - rows // 5)),
    })


def _publish_csv_snapshot(csv, snapshot_root, *, fetch_time):
    frame = pd.read_csv(csv)
    published = publish_snapshot(
        snapshot_root,
        SnapshotSpec(
            source_ids=("world_bank_projects",),
            contract_versions={"world_bank_projects": "1.0"},
            fetch_time=fetch_time,
            as_of_time=fetch_time,
            parser_git_sha="a" * 40,
            request_parameters={},
            stage_counts={"written": len(frame)},
            schema_fields=tuple(frame.columns),
            quality_checks={"valid": True},
        ),
        normalized=csv.read_bytes(),
    )
    sidecar = csv.with_name(csv.name + ".manifest.json")
    sidecar.write_text(json.dumps({
        "snapshot_id": published.snapshot_id,
        "content_sha256": _digest(csv),
    }))
    return published, sidecar


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """A seed, a runtime and a dataset of our own.

    `PROJECTS_CSV` is patched explicitly rather than steered through
    `SORA_DATA_DIR`: it is a module-level constant built from `data_dir()` at
    import, so by the time a fixture sets the variable the path is already
    fixed. Patching the environment alone would leave the retrain reading the
    repository's real dataset and this test asserting against it.
    """
    seed = tmp_path / "models"
    seed.mkdir()
    for name in ("model.pkl", "scaler.pkl", "best_threshold.pkl"):
        (seed / name).write_bytes(b"seed-" + name.encode())
    (seed / "meta.json").write_text(json.dumps({"retrained_at": "20260101_000000"}))
    (seed / "metrics.json").write_text(json.dumps({"roc_auc": 0.5}))

    data = tmp_path / "data"
    data.mkdir()
    csv = data / "projects.csv"
    _frame().to_csv(csv, index=False)

    snapshot_root = tmp_path / "snapshots"
    published, sidecar = _publish_csv_snapshot(
        csv,
        snapshot_root,
        fetch_time=datetime(2026, 10, 4, tzinfo=timezone.utc),
    )

    monkeypatch.setenv("SORA_MODELS_DIR", str(seed))
    monkeypatch.setenv("SORA_SEED_DIR", str(seed))
    monkeypatch.setenv("SORA_DATA_DIR", str(data))
    monkeypatch.setenv("SORA_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("SORA_DATA_SNAPSHOT_ROOT", str(snapshot_root))

    import app.api.retrain as retrain
    monkeypatch.setattr(retrain, "MODELS_DIR", str(seed))
    monkeypatch.setattr(retrain, "DATA_DIR", str(data), raising=False)
    monkeypatch.setattr(retrain, "PROJECTS_CSV", str(csv))
    return {
        "retrain": retrain,
        "csv": csv,
        "sidecar": sidecar,
        "snapshot_path": published.path,
        "snapshot_id": published.snapshot_id,
    }


def _meta_of(run_id):
    with open(os.path.join(staged_dir(run_id), "meta.json")) as handle:
        return json.load(handle)


def _digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def test_the_candidate_records_which_data_it_trained_on(isolated):
    """Not how many rows -- which bytes."""
    result = isolated["retrain"]._do_retrain(min_samples=10)

    meta = _meta_of(result["run_id"])
    assert "data_version" in meta, (
        "meta.json records total_samples and nothing that identifies the "
        "dataset, so the training set of a given champion cannot be named"
    )
    assert _digest(isolated["csv"])[:16] in meta["data_version"], (
        f"{meta['data_version']!r} does not identify the file the run read"
    )


def test_a_changed_dataset_produces_a_different_version(isolated):
    """The assertion that makes the first one mean anything.

    A constant satisfies "records a data_version" perfectly well -- that is
    exactly how `model_version` came to be the literal "v2.0" on every row of
    predictions_log. This observes the difference where it has to appear.
    """
    first = isolated["retrain"]._do_retrain(min_samples=10)

    _frame(rows=420, seed=11).to_csv(isolated["csv"], index=False)
    _publish_csv_snapshot(
        isolated["csv"],
        isolated["snapshot_path"].parent,
        fetch_time=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )
    second = isolated["retrain"]._do_retrain(min_samples=10)

    before = _meta_of(first["run_id"])["data_version"]
    after = _meta_of(second["run_id"])["data_version"]

    assert before != after, (
        f"both runs recorded {before!r} while reading different files, so the "
        f"field cannot distinguish two training sets and answers nothing"
    )


def test_the_journal_row_carries_the_same_version(isolated):
    """The digest is useless in a file nobody joins to the record.

    `retrain_log` is what an operator reads; `staged/<run_id>/meta.json` is
    beside an artefact that pruning eventually removes. The link is only closed
    when the row carries it too, and carries the same value.
    """
    from app.database import RetrainLog, SessionLocal

    result = isolated["retrain"]._do_retrain(min_samples=10)

    db = SessionLocal()
    try:
        row = db.query(RetrainLog).filter(
            RetrainLog.id == result["retrain_log_id"]).first()
        assert row is not None, "the run's journal row is missing"
        assert row.data_version, (
            "retrain_log.data_version is still empty, so the journal an "
            "operator reads cannot say which data the run used"
        )
        assert row.data_version == _meta_of(result["run_id"])["data_version"], (
            "the row and the candidate disagree about the dataset; two records "
            "of one run that can differ are worse than one"
        )
        assert json.loads(row.snapshot_ids_json) == [isolated["snapshot_id"]]
        assert json.loads(row.snapshot_ids_json) == _meta_of(result["run_id"])["snapshot_ids"]
    finally:
        db.close()


def test_registry_receives_the_same_snapshot_ids(isolated, monkeypatch):
    captured = {}

    def register(_model, _name, _metrics, *, snapshot_ids=None, **_kwargs):
        captured["snapshot_ids"] = list(snapshot_ids or ())
        return True

    monkeypatch.setattr("app.mlflow_tracking.log_model_registry", register)

    result = isolated["retrain"]._do_retrain(min_samples=10)

    assert captured["snapshot_ids"] == [isolated["snapshot_id"]]
    assert captured["snapshot_ids"] == _meta_of(result["run_id"])["snapshot_ids"]


def test_training_uses_the_same_bytes_that_were_verified(isolated, monkeypatch):
    from app import run_snapshots

    original = isolated["csv"].read_bytes()
    real_resolve = run_snapshots.resolve_training_dataset

    def replace_after_resolution(path):
        resolved = real_resolve(path)
        isolated["csv"].write_bytes(b"not,the,verified,dataset\n")
        return resolved

    monkeypatch.setattr(run_snapshots, "resolve_training_dataset", replace_after_resolution)

    result = isolated["retrain"]._do_retrain(min_samples=10)

    assert result["data_version"] == "sha256:" + hashlib.sha256(original).hexdigest()
    assert result["meta"]["data_rows_read"] == 400


def test_missing_snapshot_evidence_refuses_before_dataset_read(isolated, monkeypatch):
    isolated["sidecar"].unlink()
    monkeypatch.setattr(
        isolated["retrain"].pd,
        "read_csv",
        lambda *_args, **_kwargs: pytest.fail("dataset was read before provenance resolved"),
    )

    with pytest.raises(RunSnapshotError, match="manifest is missing"):
        isolated["retrain"]._do_retrain(min_samples=10)


def test_tampered_snapshot_refuses_before_dataset_read(isolated, monkeypatch):
    (isolated["snapshot_path"] / "normalized.bin").write_bytes(b"tampered")
    monkeypatch.setattr(
        isolated["retrain"].pd,
        "read_csv",
        lambda *_args, **_kwargs: pytest.fail("dataset was read before provenance resolved"),
    )

    with pytest.raises(RunSnapshotError, match="verification failed"):
        isolated["retrain"]._do_retrain(min_samples=10)
