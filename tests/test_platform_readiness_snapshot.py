"""Missing monitoring, empty windows and fresh files must not imply readiness."""
import importlib.util
import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "platform_snapshot", Path(__file__).parents[1] / "scripts/platform_readiness_snapshot.py")
snapshot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(snapshot)
NOW = datetime(2026, 11, 6, 12, tzinfo=timezone.utc).timestamp()


def healthy():
    return {key: {"status": "observed", "values": [value]} for key, value in {
        "app_up": 1, "scheduler_up": 1, "gap_count": 0, "days_examined": 26,
        "points_examined": 21, "last_success": NOW - 500}.items()}


def test_positive_control():
    assert set(snapshot.evaluate(healthy(), NOW).values()) == {"pass"}


@pytest.mark.parametrize("key,value", [
    ("app_up", 0), ("scheduler_up", 0), ("gap_count", 1),
    ("days_examined", 0), ("days_examined", 1), ("points_examined", 20),
    ("last_success", NOW - 2000), ("last_success", NOW + 1)])
def test_negative_controls(key, value):
    metrics = healthy()
    metrics[key]["values"] = [value]
    assert snapshot.evaluate(metrics, NOW)[key] == "fail"


@pytest.mark.parametrize("values", [[], [1, 1], [float("nan")], [float("inf")]])
def test_missing_ambiguous_or_nonfinite_series_is_unknown(values):
    metrics = healthy()
    metrics["app_up"]["values"] = values
    assert snapshot.evaluate(metrics, NOW)["app_up"] == "unknown"


def test_transport_failure_is_not_empty_healthy_window(monkeypatch):
    def failed(_url):
        raise OSError("server output that must not be printed")
    monkeypatch.setattr(snapshot, "read_json", failed)
    result = snapshot.query("up")
    assert result == {"status": "unknown", "reason": "query_failed"}


def test_empty_backup_directory_fails(tmp_path):
    assert snapshot.backup_metadata(tmp_path, 10000)["status"] == "fail"


def test_fresh_dump_does_not_prove_restore_or_offsite(tmp_path):
    path = tmp_path / "sora_earth_20261106T033002Z.dump"
    path.write_bytes(b"not even a valid dump")
    result = snapshot.backup_metadata(tmp_path, path.stat().st_mtime + 1)
    assert result["status"] == "pass"
    assert result["archive_integrity"] == result["offsite_recovery"] == "not_checked"


def test_stale_dump_fails(tmp_path):
    path = tmp_path / "sora_earth_20261106T033002Z.dump"
    path.write_bytes(b"fixture")
    assert snapshot.backup_metadata(tmp_path, path.stat().st_mtime + 27 * 3600)["status"] == "fail"


def test_other_database_and_symlink_do_not_count(tmp_path):
    target = tmp_path / "other.dump"
    target.write_bytes(b"fixture")
    (tmp_path / "sora_earth_fake.dump").symlink_to(target)
    assert snapshot.backup_metadata(tmp_path, target.stat().st_mtime)["count"] == 0


@pytest.mark.parametrize("payload", [
    {"status": "error"},
    {"status": "success", "data": {"resultType": "matrix", "result": []}},
    {"status": "success", "data": {"resultType": "vector", "result": [{"value": [0, "NaN"]}]}},
])
def test_invalid_query_response_is_unknown(monkeypatch, payload):
    monkeypatch.setattr(snapshot, "read_json", lambda _url: payload)
    assert snapshot.query("up")["status"] == "unknown"


def test_rolling_window_after_thirty_days():
    later = datetime(2026, 12, 6, 12, tzinfo=timezone.utc).timestamp()
    metrics = healthy()
    metrics["last_success"]["values"] = [later - 100]
    metrics["days_examined"]["values"] = [30]
    assert set(snapshot.evaluate(metrics, later).values()) == {"pass"}


def test_clock_constant_matches_coverage_source():
    source = Path(__file__).parents[1] / "app/services/observation_coverage.py"
    assignments = [node for node in ast.parse(source.read_text()).body
                   if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "M3_CLOCK_START"
                           for target in node.targets)]
    assert len(assignments) == 1
    args = [ast.literal_eval(arg) for arg in assignments[0].value.args]
    assert snapshot.CLOCK_START.isoformat() == datetime(*args).date().isoformat()
