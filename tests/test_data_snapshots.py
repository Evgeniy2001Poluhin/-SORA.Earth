import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from app.data_snapshots import (
    SnapshotError,
    SnapshotSpec,
    load_snapshot,
    publish_snapshot,
)


NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def _spec(**changes):
    values = {
        "source_ids": ("world_bank",),
        "contract_versions": {"world_bank": "1.0"},
        "fetch_time": NOW,
        "as_of_time": NOW,
        "parser_git_sha": "a" * 40,
        "request_parameters": {"country": "BRA", "api_key": "do-not-store"},
        "stage_counts": {"fetched": 2, "normalized": 2},
        "schema_fields": ("country", "indicator", "value"),
        "quality_checks": {"valid": True, "token": "also-secret"},
        "exclusions": (),
    }
    values.update(changes)
    return SnapshotSpec(**values)


def test_snapshot_is_content_addressed_durable_and_redacted(tmp_path):
    published = publish_snapshot(tmp_path, _spec(), raw=b"raw", normalized=b"normal")
    loaded = load_snapshot(tmp_path, published.snapshot_id)

    assert loaded.snapshot_id == published.snapshot_id
    assert loaded.manifest["storage_locator"] == f"snapshot:{published.snapshot_id}"
    assert loaded.manifest["request_parameters"]["api_key"] == "[redacted]"
    assert loaded.manifest["quality_checks"]["token"] == "[redacted]"
    serialized = json.dumps(loaded.manifest)
    assert "do-not-store" not in serialized
    assert "also-secret" not in serialized
    assert not any(str(tmp_path) in str(value) for value in loaded.manifest.values())


def test_logically_identical_mappings_produce_the_same_snapshot(tmp_path):
    first = publish_snapshot(tmp_path, _spec(
        request_parameters={"b": 2, "a": 1}, stage_counts={"z": 2, "a": 1}
    ), normalized=b"same")
    second = publish_snapshot(tmp_path, _spec(
        request_parameters={"a": 1, "b": 2}, stage_counts={"a": 1, "z": 2}
    ), normalized=b"same")

    assert first.snapshot_id == second.snapshot_id
    assert first.path == second.path


def test_different_normalized_bytes_produce_different_ids(tmp_path):
    first = publish_snapshot(tmp_path, _spec(), normalized=b"one")
    second = publish_snapshot(tmp_path, _spec(), normalized=b"two")

    assert first.snapshot_id != second.snapshot_id


def test_concurrent_publishers_converge_on_one_snapshot(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda _: publish_snapshot(tmp_path, _spec(), raw=b"r", normalized=b"n"),
            range(16),
        ))

    assert len({result.snapshot_id for result in results}) == 1
    snapshot_dirs = [path for path in tmp_path.iterdir() if path.is_dir()]
    assert len(snapshot_dirs) == 1


def test_tampered_payload_is_refused(tmp_path):
    published = publish_snapshot(tmp_path, _spec(), normalized=b"original")
    (published.path / "normalized.bin").write_bytes(b"tampered")

    with pytest.raises(SnapshotError, match="payload digest mismatch"):
        load_snapshot(tmp_path, published.snapshot_id)


def test_existing_corrupt_snapshot_is_not_overwritten(tmp_path):
    published = publish_snapshot(tmp_path, _spec(), normalized=b"original")
    (published.path / "manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(SnapshotError, match="disagrees with manifest id"):
        publish_snapshot(tmp_path, _spec(), normalized=b"original")
    assert (published.path / "manifest.json").read_text(encoding="utf-8") == "{}"


@pytest.mark.parametrize("field", ["fetch_time", "as_of_time", "event_time_start", "event_time_end"])
def test_naive_timestamps_are_refused(tmp_path, field):
    changes = {field: datetime(2026, 10, 4, 12, 0)}
    with pytest.raises(SnapshotError, match="timezone-aware"):
        publish_snapshot(tmp_path, _spec(**changes), normalized=b"n")


def test_inverted_event_window_is_refused(tmp_path):
    with pytest.raises(SnapshotError, match="must not be after"):
        publish_snapshot(
            tmp_path,
            _spec(
                event_time_start=datetime(2026, 10, 5, tzinfo=timezone.utc),
                event_time_end=datetime(2026, 10, 4, tzinfo=timezone.utc),
            ),
            normalized=b"n",
        )


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"source_ids": ()}, "source_ids"),
        ({"source_ids": ("world_bank", "world_bank")}, "source_ids"),
        ({"contract_versions": {}}, "contract_versions"),
        ({"contract_versions": {"world_bank": "2.0"}}, "contract version"),
        ({"parser_git_sha": "not-a-sha"}, "parser_git_sha"),
        ({"schema_fields": ()}, "schema_fields"),
        ({"stage_counts": {"rows": -1}}, "non-negative"),
        ({"request_parameters": {"x": float("nan")}}, "NaN or Infinity"),
    ],
)
def test_invalid_snapshot_metadata_is_refused(tmp_path, changes, message):
    with pytest.raises((SnapshotError, ValueError), match=message):
        publish_snapshot(tmp_path, _spec(**changes), normalized=b"n")


def test_invalid_snapshot_id_is_refused_before_filesystem_lookup(tmp_path):
    with pytest.raises(SnapshotError, match="64 lowercase"):
        load_snapshot(tmp_path, "../escape")
