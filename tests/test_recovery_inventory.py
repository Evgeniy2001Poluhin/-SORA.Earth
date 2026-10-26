"""Synthetic declarations only; no production data, models, database or container."""
from copy import deepcopy
from datetime import datetime, timezone
import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("recovery_inventory", ROOT / "app/services/recovery_inventory.py")
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)
NOW = datetime(2026, 10, 23, 13, tzinfo=timezone.utc)
SHA = "a" * 64


def metadata():
    return {
        "format_version": 1, "scenario": "cold", "incident_at": "2026-10-23T12:00:00Z",
        "release": {"git_sha": "b" * 40, "image_digest": "sha256:" + SHA, "config_sha256": SHA},
        "database": {"producer": "daily_local", "snapshot_started_at": "2026-10-22T12:00:00Z",
                     "backup_sha256": SHA, "includes_owners": False, "includes_acl": False,
                     "roles_contract_sha256": SHA, "owners_rebuild_sha256": SHA,
                     "acl_rebuild_sha256": SHA},
        "model": {"expected_source": "active", "version": "synthetic-version",
                  "run_id": "12345678-1234-4234-8234-123456789abc", "bundle_sha256": SHA,
                  "artefact_sha256": {name: SHA for name in
                                      ("model.pkl", "scaler.pkl", "best_threshold.pkl", "meta.json")}},
        "dependencies_sha256": SHA, "redis_policy_sha256": SHA, "timing_plan_sha256": SHA,
    }


def inspect(value):
    return inventory.inspect_inventory(value, now=NOW)


def test_complete_inventory_is_not_a_restore_attestation():
    manifest = metadata()
    original = deepcopy(manifest)
    result = inspect(manifest)
    assert result["status"] == "inventory_complete"
    assert result["metadata_complete"] is True
    assert result["declared_db_loss_bound_seconds"] == 86400
    for field in ("referenced_artefacts_verified", "recovery_verified", "rpo_verified", "rto_verified"):
        assert result[field] is False
    assert manifest == original


def test_required_model_files_match_loader_contract_without_importing_it():
    tree = ast.parse((ROOT / "app/model_source.py").read_text())
    declarations = [node for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "REQUIRED_ARTEFACTS"
                            for target in node.targets)]
    assert len(declarations) == 1
    assert set(ast.literal_eval(declarations[0].value)) == inventory.ARTEFACTS


@pytest.mark.parametrize("path,value,code", [
    (("format_version",), True, "manifest.format_version"),
    (("scenario",), "unknown", "manifest.scenario"),
    (("release", "git_sha"), "main", "release.git_sha"),
    (("release", "image_digest"), "app:latest", "release.image_digest"),
    (("release", "config_sha256"), None, "release.config_sha256"),
    (("database", "producer"), "unknown", "database.producer"),
    (("database", "includes_acl"), "false", "database.includes_acl"),
    (("database", "includes_acl"), True, "database.daily_local_excludes_acl"),
    (("database", "includes_owners"), True, "database.daily_local_excludes_owners"),
    (("database", "acl_rebuild_sha256"), None, "database.acl_rebuild_sha256"),
    (("database", "owners_rebuild_sha256"), None, "database.owners_rebuild_sha256"),
    (("database", "roles_contract_sha256"), None, "database.roles_contract_sha256"),
    (("database", "backup_sha256"), "bad", "database.backup_sha256"),
    (("database", "snapshot_started_at"), "2026-10-22T11:59:59Z", "timing.declared_db_loss_exceeds_24h"),
    (("database", "snapshot_started_at"), "2026-10-23T12:00:01Z", "timing.order"),
    (("incident_at",), "2026-10-23T14:00:00Z", "timing.order"),
    (("incident_at",), "2026-10-23T12:00:00", "timing.timestamps"),
    (("incident_at",), 123, "timing.timestamps"),
    (("model", "expected_source"), "fallback", "model.expected_source"),
    (("model", "version"), "", "model.version"),
    (("model", "run_id"), "not-a-run", "model.run_id"),
    (("model", "bundle_sha256"), None, "model.bundle_sha256"),
    (("model", "artefact_sha256"), {}, "model.artefact_sha256"),
    (("redis_policy_sha256",), None, "manifest.redis_policy_sha256"),
    (("dependencies_sha256",), None, "manifest.dependencies_sha256"),
    (("timing_plan_sha256",), None, "manifest.timing_plan_sha256"),
])
def test_missing_or_contradictory_declarations_block(path, value, code):
    manifest = metadata()
    target = manifest
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    result = inspect(manifest)
    assert result["metadata_complete"] is False
    assert code in result["errors"]
    assert not result["recovery_verified"]


@pytest.mark.parametrize("name", sorted(inventory.ARTEFACTS))
def test_each_missing_model_file_blocks(name):
    manifest = metadata()
    del manifest["model"]["artefact_sha256"][name]
    assert "model.artefact_sha256" in inspect(manifest)["errors"]


def test_full_database_dump_still_requires_cluster_roles():
    manifest = metadata()
    manifest["database"].update(producer="database_dump", includes_owners=True,
                                includes_acl=True, owners_rebuild_sha256=None, acl_rebuild_sha256=None)
    assert inspect(manifest)["metadata_complete"]
    manifest["database"]["roles_contract_sha256"] = None
    assert "database.roles_contract_sha256" in inspect(manifest)["errors"]


def test_seed_requires_explicit_null_run():
    manifest = metadata()
    manifest["model"]["expected_source"] = "seed"
    assert "model.seed_run_id_must_be_null" in inspect(manifest)["errors"]
    manifest["model"]["run_id"] = None
    assert inspect(manifest)["metadata_complete"]


@pytest.mark.parametrize("path", [(), ("release",), ("database",), ("model",)])
def test_unknown_fields_are_refused_without_echoing_names_or_values(path):
    manifest = metadata()
    target = manifest if not path else manifest[path[0]]
    target["private-name"] = "private-value"
    result = json.dumps(inspect(manifest))
    assert "private-" not in result
    assert '"metadata_complete": false' in result


def cli(path):
    return subprocess.run([sys.executable, "-B", str(ROOT / "scripts/recovery_inventory.py"), str(path)],
                          capture_output=True, text=True, timeout=10)


def test_cli_complete_and_blocked(tmp_path):
    path = tmp_path / "metadata.json"
    value = metadata()
    path.write_text(json.dumps(value))
    result = cli(path)
    assert result.returncode == 0
    assert json.loads(result.stdout)["recovery_verified"] is False
    value["database"]["acl_rebuild_sha256"] = None
    path.write_text(json.dumps(value))
    result = cli(path)
    assert result.returncode == 3
    assert "database.acl_rebuild_sha256" in result.stdout


@pytest.mark.parametrize("raw", [b'{"private-value":', b'{"x":1,"x":2}', b'\xff', b' ' * 65537])
def test_cli_rejects_malformed_duplicate_or_oversized_metadata(tmp_path, raw):
    path = tmp_path / "private-filename.json"
    path.write_bytes(raw)
    result = cli(path)
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "invalid_input"
    assert "private-" not in result.stdout + result.stderr


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="POSIX no-follow required")
def test_cli_refuses_symlink(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps(metadata()))
    link = tmp_path / "link.json"
    link.symlink_to(source)
    assert cli(link).returncode == 2


def test_naive_assessment_time_refused():
    with pytest.raises(ValueError, match="aware assessment"):
        inventory.inspect_inventory(metadata(), now=NOW.replace(tzinfo=None))
