"""The C8 before/after comparison refuses to report when either side is not what its commit says.

scripts/map_score_c8_comparison.py reads both sides from exact commits. These tests pin the
guards that make that claim true, one per refusal:

1. A loaded app module whose bytes are not the git blob of its commit is refused.
2. The pinned table the new side read must hash to the archived bytes.
3. manifest.json must be the manifest that PINNED_MANIFEST_SHA256 declares, both in the
   runner's report and in the archived app/map_inputs.py source.
4. An app module loaded from outside the archive makes the runner exit 3 (EXIT_PROVENANCE).
5. A revision that resolves to a tree, not a commit, is refused.
6. A commit with no app tree is refused.
7. The report is byte-identical across two runs.

Most tests call run_side("new", HEAD) and need no network and no old commit. The end-to-end
determinism test needs OLD_SHA in the local object store, so it skips where that commit is
absent (a shallow clone).
"""
import hashlib
import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "map_score_c8_comparison.py"


def _load():
    spec = importlib.util.spec_from_file_location("map_score_c8_comparison", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _commit_present(sha):
    return subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=ROOT,
                          capture_output=True).returncode == 0


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _head(mod):
    return mod.resolve_commit("HEAD")


# ---------- 1. loaded module bytes must be the commit's blob ----------

def test_loaded_app_module_with_bytes_other_than_its_blob_is_refused(monkeypatch):
    mod = _load()
    real_materialise = mod.materialise

    def tampered(sha, paths, dest):
        real_materialise(sha, paths, dest)
        target = Path(dest) / "app" / "external_data.py"
        target.write_bytes(target.read_bytes() + b"\n# tampered after archive\n")

    monkeypatch.setattr(mod, "materialise", tampered)
    with pytest.raises(mod.Refusal, match="bytes are not the blob"):
        mod.run_side("new", _head(mod))


def test_untampered_new_side_runs_and_every_module_matches_its_blob():
    mod = _load()
    side = mod.run_side("new", _head(mod))
    assert side["modules"], "no app module was recorded as loaded"
    assert any(rel == "app/external_data.py" for rel, _ in side["modules"])


# ---------- 2 and 3. the pinned table and the pinned manifest ----------

def _archive_table(mod, dest):
    mod.materialise(_head(mod), ["app", "data/map_inputs"], dest)
    tables = sorted(p.parent for p in (Path(dest) / "data" / "map_inputs").glob("*/map_inputs.json"))
    assert len(tables) == 1, f"expected one pinned table in the archive, found {tables}"
    table_dir = tables[0]
    rel_dir = table_dir.relative_to(Path(dest)).as_posix()
    source = (Path(dest) / "app" / "map_inputs.py").read_text(encoding="utf-8")
    declared = re.findall(r'^PINNED_MANIFEST_SHA256 = "([0-9a-f]{64})"$', source, flags=re.MULTILINE)
    assert len(declared) == 1
    payload = {"table": {
        "available": True,
        "reason": None,
        "table_dir": rel_dir,
        "table_sha256": _sha256((table_dir / "map_inputs.json").read_bytes()),
        "manifest_sha256": _sha256((table_dir / "manifest.json").read_bytes()),
        "declared_manifest_sha256": declared[0],
    }}
    return payload


def test_archived_table_verifies_as_read(tmp_path):
    mod = _load()
    payload = _archive_table(mod, tmp_path)
    digests = mod.table_digests(tmp_path, payload)
    assert digests["table_sha256"] == payload["table"]["table_sha256"]


def test_table_changed_after_the_new_side_read_it_is_refused(tmp_path):
    mod = _load()
    payload = _archive_table(mod, tmp_path)
    table = next((tmp_path / payload["table"]["table_dir"]).glob("map_inputs.json"))
    table.write_bytes(table.read_bytes() + b" ")  # still valid JSON, different bytes
    with pytest.raises(mod.Refusal, match="the table the new side read differs from the archived bytes"):
        mod.table_digests(tmp_path, payload)


def test_manifest_that_is_not_the_declared_pin_is_refused(tmp_path):
    mod = _load()
    payload = _archive_table(mod, tmp_path)
    payload["table"]["declared_manifest_sha256"] = "0" * 64
    with pytest.raises(mod.Refusal, match="is not the manifest declared as PINNED_MANIFEST_SHA256"):
        mod.table_digests(tmp_path, payload)


def test_archived_source_pin_that_is_not_the_manifest_is_refused(tmp_path):
    mod = _load()
    payload = _archive_table(mod, tmp_path)
    source = tmp_path / "app" / "map_inputs.py"
    source.write_text(re.sub(r'^PINNED_MANIFEST_SHA256 = "[0-9a-f]{64}"$',
                             'PINNED_MANIFEST_SHA256 = "' + "f" * 64 + '"',
                             source.read_text(encoding="utf-8"), flags=re.MULTILINE),
                      encoding="utf-8")
    with pytest.raises(mod.Refusal, match="is not the manifest declared as PINNED_MANIFEST_SHA256"):
        mod.table_digests(tmp_path, payload)


# ---------- 4. a module from outside the archive makes the runner exit 3 ----------

def test_module_loaded_from_outside_the_archive_exits_3(monkeypatch, tmp_path):
    mod = _load()
    planted = (
        "import types as _types, sys as _sys\n"
        "_planted = _types.ModuleType('app.planted')\n"
        "_planted.__file__ = '/nonexistent/outside/app/planted.py'\n"
        "_sys.modules['app.planted'] = _planted\n"
    )
    monkeypatch.setattr(mod, "RUNNER", planted + mod.RUNNER)
    mod.materialise(_head(mod), ["app", "data/map_inputs"], tmp_path)
    with pytest.raises(mod.Refusal) as info:
        mod.run_interpreter("new", tmp_path)
    message = str(info.value)
    assert "(exit 3)" in message
    assert "app modules loaded from outside the archive" in message
    assert "app.planted" in message


# ---------- 5. a revision must resolve to a commit ----------

def test_a_tree_is_refused_as_a_commit():
    mod = _load()
    tree = subprocess.run(["git", "rev-parse", "--verify", "HEAD^{tree}"], cwd=ROOT,
                          capture_output=True, text=True, check=True).stdout.strip()
    with pytest.raises(mod.Refusal, match="does not resolve to a commit"):
        mod.resolve_commit(tree)


def test_head_resolves_to_a_full_commit_sha():
    mod = _load()
    assert re.fullmatch(r"[0-9a-f]{40}", _head(mod))


# ---------- 6. a commit must have an app tree ----------

def test_commit_without_an_app_tree_is_refused(monkeypatch):
    mod = _load()
    real_object_id = mod.object_id

    def without_app(spec):
        if spec.endswith(":app"):
            return None
        return real_object_id(spec)

    monkeypatch.setattr(mod, "object_id", without_app)
    with pytest.raises(mod.Refusal, match="has no app tree"):
        mod.run_side("new", _head(mod))


# ---------- 7. output is deterministic ----------

def test_new_side_payload_is_identical_across_two_runs():
    mod = _load()
    sha = _head(mod)
    first = json.dumps(mod.run_side("new", sha)["payload"], sort_keys=True)
    second = json.dumps(mod.run_side("new", sha)["payload"], sort_keys=True)
    assert first == second


@pytest.mark.skipif(not _commit_present("e4009ec8ca8c6b25ab3616983c3736b63a22b594"),
                    reason="the OLD_SHA commit is not in this clone (shallow checkout)")
def test_report_is_byte_identical_across_two_runs(capsys):
    mod = _load()
    # --allow-dirty-script: this test checks determinism, not the commit guard, and the
    # script may be uncommitted while it is being changed.
    args = ["--new-sha", "HEAD", "--allow-dirty-script"]
    assert mod.main(args) == 0
    first = capsys.readouterr().out
    assert mod.main(args) == 0
    second = capsys.readouterr().out
    assert first == second
    assert "map-c8-" not in first, "a temporary directory name leaked into the report"
