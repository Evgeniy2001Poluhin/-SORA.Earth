"""World Bank dataset snapshots, exercised without publisher network access."""
from __future__ import annotations

import json
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from dataset_identity import (  # noqa: E402
    StageCounts,
    publish_world_bank_snapshot,
    write_manifest,
)
from app.data_snapshots import load_snapshot  # noqa: E402
from app.ingesters.data_contracts import source_contract  # noqa: E402


SHA = "a" * 40
FETCHED_AT = "2026-10-04T12:30:00+00:00"


def _publish(tmp_path: Path, *, raw: bool = True):
    output = tmp_path / "projects.csv"
    output.write_bytes(b"source,source_project_id\nworldbank,P1\n")
    raw_path = tmp_path / "raw.json"
    if raw:
        raw_path.write_text('[{"id":"P1"}]', encoding="utf-8")
    return publish_world_bank_snapshot(
        str(tmp_path / "snapshots"),
        output_path=str(output),
        raw_path=str(raw_path) if raw else None,
        fetch_timestamp=FETCHED_AT,
        commit_sha=SHA,
        query_params={"max_projects": 1, "api_key": "must-not-survive"},
        stage_counts=[StageCounts("fetched", 1), StageCounts("written", 1)],
        columns=("source", "source_project_id"),
        unique_ids=1,
        duplicate_ids=0,
    )


def test_projects_and_indicator_contracts_are_distinct():
    projects = source_contract("world_bank_projects")
    indicators = source_contract("world_bank")

    assert projects["access"]["endpoint"] == "https://search.worldbank.org/api/v2/projects"
    assert "source_project_id" in {field["name"] for field in projects["fields"]}
    assert projects["parser"]["module"] == "scripts.fetch_wb_projects"
    assert projects["source_id"] != indicators["source_id"]


def test_snapshot_is_deterministic_verified_and_redacted(tmp_path):
    first = _publish(tmp_path)
    second = _publish(tmp_path)
    loaded = load_snapshot(tmp_path / "snapshots", first.snapshot_id)

    assert first.snapshot_id == second.snapshot_id
    assert loaded.manifest["source_ids"] == ["world_bank", "world_bank_projects"]
    assert loaded.manifest["request_parameters"]["api_key"] == "[redacted]"
    assert loaded.manifest["quality_checks"]["unique_source_ids"] == 1
    assert loaded.manifest["quality_checks"]["raw_representation"] == "decoded_project_records_json"
    assert (first.path / "normalized.bin").read_bytes().startswith(b"source,")
    assert (first.path / "raw.bin").read_bytes() == b'[{"id":"P1"}]'


def test_snapshot_without_raw_transport_is_explicit(tmp_path):
    published = _publish(tmp_path, raw=False)
    assert published.manifest["raw"] is None
    assert published.manifest["quality_checks"]["raw_representation"] == "not_retained"
    assert "publisher transport bytes are not retained" in published.manifest["exclusions"]


def test_duplicate_stage_names_fail_before_publication(tmp_path):
    output = tmp_path / "projects.csv"
    output.write_text("id\nP1\n", encoding="utf-8")
    try:
        publish_world_bank_snapshot(
            str(tmp_path / "snapshots"), output_path=str(output), raw_path=None,
            fetch_timestamp=FETCHED_AT, commit_sha=SHA, query_params={},
            stage_counts=[StageCounts("same", 1), StageCounts("same", 2)],
            columns=("id",), unique_ids=1, duplicate_ids=0,
        )
    except ValueError as exc:
        assert "stage names must be unique" in str(exc)
    else:
        raise AssertionError("duplicate stage names were accepted")


def test_legacy_manifest_is_compatible_and_links_snapshot(tmp_path):
    output = tmp_path / "projects.csv"
    output.write_text("id\nP1\n", encoding="utf-8")
    manifest = tmp_path / "projects.csv.manifest.json"
    snapshot_id = "b" * 64
    write_manifest(
        str(manifest), source_url="https://example.invalid/projects",
        query_params={}, fetch_timestamp=FETCHED_AT, commit_sha=SHA,
        stage_counts=[StageCounts("written", 1)], unique_ids=1,
        duplicate_ids=0, columns=("id",), output_path=str(output),
        snapshot_id=snapshot_id,
    )

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["snapshot_id"] == snapshot_id
    assert payload["unique_ids"] == 1
    assert payload["content_sha256"]


def test_both_cli_entrypoints_offer_opt_in_snapshot_publication():
    for script in ("enrich_worldbank_dataset.py", "fetch_wb_projects.py"):
        text = (SCRIPTS / script).read_text(encoding="utf-8")
        assert '"--snapshot-root"' in text
        assert "publish_world_bank_snapshot(" in text
        assert 'os.getenv("SORA_DATA_SNAPSHOT_ROOT")' in text
