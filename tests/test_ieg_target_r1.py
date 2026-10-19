"""Offline tests for scripts/ieg_target_r1.py (R1 spec, sections 1-12). Synthetic data only.

Nothing here reads real ratings. Every test records each file opened through
builtins.open or io.open; a test fails if any recorded path lies under the
research data root.
"""

from __future__ import annotations

import builtins
import csv
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "ieg_target_r1.py"
_HARNESS = _REPO / "tests" / "r1_synthetic_harness.py"
_FORBIDDEN_ROOT = "sora-research"

_spec_h = importlib.util.spec_from_file_location("r1_synthetic_harness", _HARNESS)
H = importlib.util.module_from_spec(_spec_h)
sys.modules[_spec_h.name] = H
_spec_h.loader.exec_module(H)
r1 = H.r1

ANALYTIC = (
    "target_rows.csv",
    "target_exclusions.csv",
    "final_ids_development.txt",
    "final_ids_holdout.txt",
    "country_weights.json",
    "d1_verdict.json",
    "target_qa.json",
)
MARKERS = tuple(str(v) for k, v in H.FORBIDDEN_MARKERS.items() if isinstance(v, str))
REMOVE = object()
ALL_PROJECTS = H.DEMO_PROJECTS


@pytest.fixture(autouse=True)
def file_opens(monkeypatch):
    """Record every path opened in this test. Fail at teardown if one is under the research root."""
    current: list[str] = []
    real_open = builtins.open

    def recording_open(file, *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)):
            current.append(os.fsdecode(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", recording_open)
    monkeypatch.setattr(io, "open", recording_open)
    yield current
    bad = [p for p in current if _FORBIDDEN_ROOT in p]
    assert not bad, f"test opened a path under the research root: {bad}"


def _run(projects, **kwargs):
    world = H.make_world(projects, **kwargs)
    return world, H.run_pipeline(world)


def _csv(result, name):
    return list(csv.DictReader(io.StringIO(result.files[name].decode("utf-8"), newline="")))


def _json(result, name):
    return json.loads(result.files[name].decode("utf-8"))


def _write(out, result):
    return r1.write_outputs(result, out)


def _permit(tmp_path, *, raw=None, remove=(), extra=None, **changes):
    if raw is not None:
        data = raw
    else:
        obj = {
            "stage": "R1",
            "r0_receipt_sha256": "a" * 64,
            "builder_sha256": r1.sha256_bytes(r1.BUILDER_PATH.read_bytes()),
            "vocabulary_sha256": "b" * 64,
            "d1_policy_sha256": "c" * 64,
            "structural_receipt_manifest_sha256": r1.REAL_PINS.structural_manifest_sha,
            "owner_decision": "разрешаю R1",
            "date_utc": "2026-10-20T10:00:00Z",
        }
        obj.update(changes)
        for key in remove:
            obj.pop(key)
        if extra:
            obj.update(extra)
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    path = tmp_path / "permit.json"
    path.write_bytes(data)
    return path


def _trust(tmp_path, value):
    path = tmp_path / "trust.json"
    path.write_text(json.dumps({"r1_permit_sha256": value}), encoding="utf-8")
    return path


def _trusting(tmp_path, permit):
    return _trust(tmp_path, r1.sha256_bytes(permit.read_bytes()))


def _proj(pid, country, *records):
    return {"pid": pid, "country": country, "records": list(records)}


def _rec(fy, outcome=H.ABSENT):
    rec = {"fy": fy}
    if outcome is not H.ABSENT:
        rec["outcome"] = outcome
    return rec


# ---------------------------------------------------------------- section 3: rules T1-T4

def test_six_literals_map_to_one_or_zero():
    projects = [
        _proj("P100001", "ARG", _rec(2010, "Highly Satisfactory")),
        _proj("P100002", "ARG", _rec(2011, "Satisfactory")),
        _proj("P100003", "BRA", _rec(2012, "Moderately Satisfactory")),
        _proj("P100004", "BRA", _rec(2013, "Moderately Unsatisfactory")),
        _proj("P100005", "CHL", _rec(2014, "Unsatisfactory")),
        _proj("P100006", "CHL", _rec(2015, "Highly Unsatisfactory")),
    ]
    _, res = _run(projects)
    assert res.status == "ok"
    got = {r["project_id"]: r["ieg_outcome_satisfactory"] for r in _csv(res, "target_rows.csv")}
    assert got == {
        "P100001": "1", "P100002": "1", "P100003": "1",
        "P100004": "0", "P100005": "0", "P100006": "0",
    }


def test_not_rated_is_T2_and_never_a_target():
    _, res = _run([_proj("P100001", "ARG", _rec(2012, "Not Rated")), _proj("P100002", "ARG", _rec(2012, "Satisfactory"))])
    assert res.status == "ok"
    rows = _csv(res, "target_exclusions.csv")
    assert [(r["project_id"], r["rule"], r["reason"]) for r in rows] == [
        ("P100001", "T2", "outcome_not_rated")]
    assert [r["project_id"] for r in _csv(res, "target_rows.csv")] == ["P100002"]


def test_missing_null_and_empty_are_T1():
    projects = [
        _proj("P100001", "ARG", _rec(2012, H.ABSENT)),
        _proj("P100002", "ARG", _rec(2012, None)),
        _proj("P100003", "ARG", _rec(2012, "")),
        _proj("P100004", "ARG", _rec(2012, "   \t ")),
        _proj("P100005", "ARG", _rec(2012, "Satisfactory")),
    ]
    _, res = _run(projects)
    assert res.status == "ok"
    rules = {r["project_id"]: r["rule"] for r in _csv(res, "target_exclusions.csv")}
    assert rules == {"P100001": "T1", "P100002": "T1", "P100003": "T1", "P100004": "T1"}
    assert [r["project_id"] for r in _csv(res, "target_rows.csv")] == ["P100005"]


def test_unknown_literal_blocks_and_is_hashed_in_the_receipt(tmp_path):
    world, res = _run([_proj("P100001", "ARG", _rec(2012, "Excellent")), _proj("P100002", "ARG", _rec(2012, "Satisfactory"))])
    assert res.status == "blocked"
    assert set(res.files) == {"blocked_receipt.json"}
    receipt = _json(res, "blocked_receipt.json")
    literal_sha = hashlib.sha256(b'["string","Excellent"]').hexdigest()
    assert receipt["unknown_count"] == 1
    assert receipt["unknown_values"] == [{"type": "string", "sha256": literal_sha, "count": 1}]
    assert b"Excellent" not in res.files["blocked_receipt.json"]


def test_literal_case_is_not_normalised():
    _, res = _run([_proj("P100001", "ARG", _rec(2012, "satisfactory"))])
    assert res.status == "blocked"
    receipt = _json(res, "blocked_receipt.json")
    assert receipt["unknown_values"][0]["sha256"] == hashlib.sha256(b'["string","satisfactory"]').hexdigest()


def test_whitespace_is_collapsed_before_mapping():
    _, res = _run([_proj("P100001", "ARG", _rec(2012, "  Highly   Satisfactory  "))])
    assert res.status == "ok"
    row = _csv(res, "target_rows.csv")[0]
    assert row["ieg_outcome_satisfactory"] == "1"
    assert row["outcome_raw_canonical"] == "Highly Satisfactory"


def test_same_literal_on_two_max_fy_records_is_one_row_with_both_references():
    projects = [_proj("P100001", "ARG", _rec(2019, "Satisfactory"), _rec(2019, "Satisfactory"))]
    _, res = _run(projects)
    assert res.status == "ok"
    rows = _csv(res, "target_rows.csv")
    assert len(rows) == 1 and rows[0]["n_source_records"] == "2"
    audit = _csv(res, "target_audit.csv")
    assert len(audit) == 2
    assert {a["ieg_index"] for a in audit} == {"0", "1"}
    assert all(a["used"] == "1" for a in audit)


def test_T4_when_one_record_is_not_rated_still_excludes_the_project():
    _, res = _run([_proj("P100001", "ARG", _rec(2018, "Satisfactory"), _rec(2018, "Not Rated"))])
    assert res.status == "ok"
    rows = _csv(res, "target_exclusions.csv")
    assert len(rows) == 1 and rows[0]["rule"] == "T4" and rows[0]["reason"] == "latest_rating_conflict"
    literals = json.loads(rows[0]["literals"])
    assert sorted(literals) == [["string", "Not Rated"], ["string", "Satisfactory"]]
    assert _csv(res, "target_rows.csv") == []


def test_T4_missing_or_null_against_a_string_is_a_conflict():
    projects = [
        _proj("P100001", "ARG", _rec(2018, H.ABSENT), _rec(2018, "Satisfactory")),
        _proj("P100002", "ARG", _rec(2018, None), _rec(2018, "Satisfactory")),
    ]
    _, res = _run(projects)
    assert res.status == "ok"
    assert [(r["project_id"], r["rule"]) for r in _csv(res, "target_exclusions.csv")] == [
        ("P100001", "T4"), ("P100002", "T4")]


def test_T4_two_literals_of_the_same_class_still_conflict():
    _, res = _run([_proj("P100001", "ARG", _rec(2018, "Satisfactory"), _rec(2018, "Moderately Satisfactory"))])
    assert res.status == "ok"
    assert [r["rule"] for r in _csv(res, "target_exclusions.csv")] == ["T4"]


def test_string_NULL_and_MISSING_are_not_the_absent_kinds():
    _, res_null = _run([_proj("P100001", "ARG", _rec(2012, None))])
    assert res_null.status == "ok"
    assert [r["rule"] for r in _csv(res_null, "target_exclusions.csv")] == ["T1"]
    _, res_str = _run([_proj("P100001", "ARG", _rec(2012, "NULL"))])
    assert res_str.status == "blocked"
    assert _json(res_str, "blocked_receipt.json")["unknown_values"][0]["type"] == "string"
    _, res_miss = _run([_proj("P100001", "ARG", _rec(2012, "MISSING"))])
    assert res_miss.status == "blocked"


def test_each_json_type_is_nonstring_and_blocks():
    projects = [
        _proj("P100001", "ARG", _rec(2012, True)),
        _proj("P100002", "ARG", _rec(2012, 1)),
        _proj("P100003", "ARG", _rec(2012, 1.5)),
        _proj("P100004", "ARG", _rec(2012, [1])),
        _proj("P100005", "ARG", _rec(2012, {"a": 1})),
    ]
    _, res = _run(projects)
    assert res.status == "blocked"
    receipt = _json(res, "blocked_receipt.json")
    assert receipt["unknown_count"] == 5
    assert {v["type"] for v in receipt["unknown_values"]} == {
        "nonstring:bool", "nonstring:int", "nonstring:float", "nonstring:list", "nonstring:object"}


def test_true_and_one_are_different_values():
    _, res = _run([_proj("P100001", "ARG", _rec(2012, True)), _proj("P100002", "ARG", _rec(2012, 1))])
    assert res.status == "blocked"
    assert _json(res, "blocked_receipt.json")["unknown_count"] == 2


def test_unknown_blocks_even_when_the_project_is_T4():
    _, res = _run([_proj("P100001", "ARG", _rec(2018, "Foo"), _rec(2018, "Satisfactory"))])
    assert res.status == "blocked"
    receipt = _json(res, "blocked_receipt.json")
    assert receipt["projects_by_rule"]["T4"] == 1
    assert receipt["unknown_count"] == 1


def test_records_below_the_maximum_fy_are_superseded_and_ignored():
    projects = [_proj("P100001", "ARG", _rec(2009, "Foo"), _rec(2010, "Unsatisfactory"), _rec(2015, "Satisfactory"))]
    _, res = _run(projects)
    assert res.status == "ok"
    row = _csv(res, "target_rows.csv")[0]
    assert row["ieg_outcome_satisfactory"] == "1" and row["n_source_records"] == "1"
    audit = _csv(res, "target_audit.csv")
    assert [a["outcome_canonical"] for a in audit] == ['["string","Satisfactory"]']


# ---------------------------------------------------------------- section 4 and 10.4: outputs and audit

def test_audit_records_original_json_and_typed_canonical_value():
    projects = [
        _proj("P100001", "ARG", _rec(2012, H.ABSENT)),
        _proj("P100002", "ARG", _rec(2012, None)),
        _proj("P100003", "ARG", _rec(2012, "Satisfactory")),
    ]
    _, res = _run(projects)
    audit = {a["project_id"]: a for a in _csv(res, "target_audit.csv")}
    assert audit["P100001"]["outcome_present"] == "0"
    assert audit["P100001"]["outcome_original_json"] == ""
    assert audit["P100001"]["outcome_canonical"] == '["missing"]'
    assert audit["P100001"]["used"] == "0"
    assert audit["P100002"]["outcome_present"] == "1"
    assert audit["P100002"]["outcome_original_json"] == "null"
    assert audit["P100002"]["outcome_canonical"] == '["null"]'
    assert audit["P100003"]["outcome_original_json"] == '"Satisfactory"'
    assert audit["P100003"]["used"] == "1"


def test_outputs_have_the_documented_columns_and_split_counts():
    _, res = _run(ALL_PROJECTS)
    assert set(_csv(res, "target_rows.csv")[0]) == {
        "project_id", "split", "ieg_outcome_satisfactory", "outcome_raw_canonical", "n_source_records"}
    assert set(_csv(res, "target_exclusions.csv")[0]) == {"project_id", "split", "rule", "reason", "literals"}
    qa = _json(res, "target_qa.json")
    assert qa["candidates"] == {"development": 4, "holdout": 3, "total": 7}
    assert qa["class_counts"]["development"] == {"1": 2, "0": 2}
    assert qa["class_counts"]["holdout"] == {"1": 1, "0": 1}


def test_final_ids_are_sorted_with_trailing_newline():
    _, res = _run(list(reversed(ALL_PROJECTS)))
    text = res.files["final_ids_development.txt"].decode("utf-8")
    assert text == "P000001\nP000002\nP000003\nP000004\n"


def test_written_manifest_hashes_match_the_bytes_on_disk(tmp_path):
    _, res = _run(ALL_PROJECTS)
    final = _write(tmp_path / "out", res)
    manifest = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
    for name, meta in manifest["outputs"].items():
        data = (final / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == meta["sha256"]
        assert len(data) == meta["bytes"]
    assert manifest["inputs"] == dict(res.input_sha256)
    assert manifest["run"]["builder_sha256"] == r1.sha256_bytes(_SCRIPT.read_bytes())
    assert manifest["rule_version"] == "1"
    assert not (tmp_path / "out.partial").exists()


def test_write_outputs_refuses_existing_folders(tmp_path):
    _, res = _run(ALL_PROJECTS)
    for blocker in ("out", "out.partial", "out.blocked"):
        target = tmp_path / "out"
        (tmp_path / blocker).mkdir()
        with pytest.raises(r1.OutputRefused):
            _write(target, res)
        (tmp_path / blocker).rmdir()
    assert not (tmp_path / "out").exists()


# ---------------------------------------------------------------- section 11.5 and 12.4: blocked folder

def test_blocked_folder_holds_only_the_receipt_and_no_project_id(tmp_path):
    projects = ALL_PROJECTS + [_proj("P999111", "PER", _rec(2024, "Excellent"))]
    _, res = _run(projects)
    assert res.status == "blocked"
    final = _write(tmp_path / "out", res)
    assert final == tmp_path / "out.blocked"
    assert sorted(p.name for p in final.iterdir()) == ["blocked_receipt.json"]
    assert not (tmp_path / "out").exists() and not (tmp_path / "out.partial").exists()
    blob = (final / "blocked_receipt.json").read_bytes()
    for proj in projects:
        assert proj["pid"].encode("ascii") not in blob
    assert b"Excellent" not in blob


# ---------------------------------------------------------------- section 10.5: receipt consistency

def _hook_pid(pid, mutate):
    def hook(rows, split):
        for row in rows:
            if row["project_id"] == pid:
                mutate(row, split)
    return hook


def test_split_overlap_is_refused():
    def hook(rows, split):
        dev = split["development"][0]
        split["holdout"] = sorted(split["holdout"] + [dev])
    world = H.make_world(ALL_PROJECTS, receipt_hook=hook)
    with pytest.raises(r1.IntegrityError, match="both windows"):
        r1.validate_bytes(world.blobs, world.pins)


def test_row_window_disagreeing_with_split_file_is_refused():
    """The split file moves P000001 to holdout while its row (FY 2012) still says development."""
    def mutate(row, split):
        split["development"] = sorted(p for p in split["development"] if p != "P000001")
        split["holdout"] = sorted(split["holdout"] + ["P000001"])
    world = H.make_world(ALL_PROJECTS, receipt_hook=_hook_pid("P000001", mutate))
    with pytest.raises(r1.IntegrityError, match="not listed"):
        r1.validate_bytes(world.blobs, world.pins)


def _move_to(pid, window):
    def mutate(row, split):
        row["window"] = window
        for key in split:
            split[key] = sorted(p for p in split[key] if p != pid)
        split[window] = sorted(split[window] + [pid])
    return mutate


def test_development_candidate_above_2019_is_refused():
    projects = [_proj("P300001", "ARG", _rec(2020, "Satisfactory"))]
    world = H.make_world(projects, receipt_hook=_hook_pid("P300001", _move_to("P300001", "development")))
    with pytest.raises(r1.IntegrityError, match="development"):
        r1.validate_bytes(world.blobs, world.pins)


def test_holdout_candidate_below_2020_is_refused():
    projects = [_proj("P300001", "ARG", _rec(2018, "Satisfactory"))]
    world = H.make_world(projects, receipt_hook=_hook_pid("P300001", _move_to("P300001", "holdout")))
    with pytest.raises(r1.IntegrityError, match="holdout"):
        r1.validate_bytes(world.blobs, world.pins)


def test_authoritative_records_must_match_the_snapshot():
    def mutate(row, split):
        row["authoritative_records"] = row["authoritative_records"] + ";ieg/page-00009.json#0"
    world = H.make_world(ALL_PROJECTS, receipt_hook=_hook_pid("P000002", mutate))
    with pytest.raises(r1.IntegrityError, match="authoritative records"):
        r1.validate_bytes(world.blobs, world.pins)


def test_receipt_maximum_fy_must_match_the_snapshot():
    def mutate(row, split):
        row["evaluation_fy"] = "2014"
    world = H.make_world(ALL_PROJECTS, receipt_hook=_hook_pid("P000002", mutate))
    with pytest.raises(r1.IntegrityError, match="maximum evaluation_fy"):
        r1.validate_bytes(world.blobs, world.pins)


def test_candidate_missing_from_the_snapshot_is_refused_not_dropped():
    def hook(rows, split):
        rows.append({"project_id": "P999999", "window": "development", "evaluation_fy": "2012",
                     "wb_code": "ARG", "authoritative_records": "ieg/page-00000.json#0"})
        split["development"] = sorted(split["development"] + ["P999999"])
    world = H.make_world(ALL_PROJECTS, receipt_hook=hook)
    with pytest.raises(r1.IntegrityError, match="not in the snapshot"):
        r1.validate_bytes(world.blobs, world.pins)


def test_duplicate_candidate_is_refused():
    def hook(rows, split):
        rows.append(dict(rows[0]))
    world = H.make_world(ALL_PROJECTS, receipt_hook=hook)
    with pytest.raises(r1.IntegrityError, match="twice"):
        r1.validate_bytes(world.blobs, world.pins)


def test_candidate_row_and_split_file_must_name_the_same_projects():
    def hook(rows, split):
        split["holdout"] = sorted(p for p in split["holdout"] if p != "P000005")
    world = H.make_world(ALL_PROJECTS, receipt_hook=hook)
    with pytest.raises(r1.IntegrityError, match="not listed|different projects"):
        r1.validate_bytes(world.blobs, world.pins)


# ---------------------------------------------------------------- section 6: pins

@pytest.fixture
def demo():
    return H.make_world(ALL_PROJECTS)


def test_every_input_byte_change_is_refused(demo):
    for key in list(demo.blobs):
        blobs = dict(demo.blobs)
        blobs[key] = blobs[key] + b" "
        with pytest.raises(r1.PinRefused):
            r1.validate_bytes(blobs, demo.pins)


@pytest.mark.parametrize("field", ["snapshot_manifest_sha", "snapshot_page_digest", "structural_manifest_sha",
                                   "vocabulary_sha", "d1_policy_sha"])
def test_unset_pin_refuses(demo, field):
    with pytest.raises(r1.PinRefused, match="no pin is set"):
        r1.validate_bytes(demo.blobs, replace(demo.pins, **{field: None}))


@pytest.mark.parametrize("field", ["snapshot_manifest_sha", "snapshot_page_digest", "structural_manifest_sha",
                                   "vocabulary_sha", "d1_policy_sha"])
def test_wrong_pin_refuses(demo, field):
    with pytest.raises(r1.PinRefused):
        r1.validate_bytes(demo.blobs, replace(demo.pins, **{field: "0" * 64}))


def test_refused_validation_writes_no_folder(demo, tmp_path):
    blobs = dict(demo.blobs)
    blobs[r1.K_D1] = b"{}"
    with pytest.raises(r1.PinRefused):
        r1.validate_bytes(blobs, demo.pins)
    assert list(tmp_path.iterdir()) == []


def test_real_pins_have_the_signed_receipt_and_unset_vocabulary_and_d1():
    assert r1.REAL_PINS.snapshot_manifest_sha == "b3d43f8033367d79bb14ace692f8cada7068e98a3540fdfbbc0a1bea2505474c"
    assert r1.REAL_PINS.snapshot_page_digest == "960d776e8db25a59abcb3562e921c1643ace32466d71682c017b2912dc43d4f3"
    assert r1.REAL_PINS.structural_manifest_sha == "36b541dbf74f87762a292125ac454c981ac7381ddce05faae4b97514a2c53ef4"
    assert r1.REAL_PINS.vocabulary_sha is None and r1.REAL_PINS.d1_policy_sha is None


# ---------------------------------------------------------------- section 6, 11.6, 12.1: permit, trust, exit 3

def test_shipped_trust_file_holds_null():
    assert json.loads((_REPO / "scripts" / "ieg_target_r1_trust.json").read_text(encoding="utf-8")) == {
        "r1_permit_sha256": None}


def test_real_mode_refuses_with_the_shipped_trust_and_opens_only_the_trust(tmp_path, file_opens):
    permit = _permit(tmp_path)
    out = tmp_path / "out"
    file_opens.clear()
    code = r1.main(["--real", "--r1-permit", str(permit), "--out", str(out)])
    assert code == 3
    assert not out.exists()
    assert set(file_opens) <= {str(r1.TRUST_PATH)}


def test_real_mode_with_a_trusted_permit_still_refuses_before_any_data(tmp_path, file_opens):
    permit = _permit(tmp_path)
    trust = _trusting(tmp_path, permit)
    file_opens.clear()
    with pytest.raises(r1.PermitRefused, match="vocabulary pin"):
        r1.load_real(permit, trust_path=trust)
    assert set(file_opens) <= {str(permit), str(trust), str(r1.BUILDER_PATH)}
    assert not any("snapshot" in p or "structural" in p for p in file_opens)


@pytest.mark.parametrize("argv", [
    [],
    ["--real"],
    ["--real", "--r1-permit"],
    ["--real", "--r1-permit", "p.json"],
    ["--real", "--out", "d", "--r1-permit", "p.json"],
    ["--r1-permit", "p.json", "--out", "d"],
    ["--synthetic", "d"],
    ["--real", "--r1-permit", "p.json", "--out", "d", "--extra"],
    ["--scenario", "blocked", "--out", "d"],
])
def test_any_other_command_line_exits_3_and_opens_nothing(argv, file_opens, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert r1.main(argv) == 3
    assert file_opens == []
    assert not any(p.name == "d" for p in tmp_path.iterdir())


def test_help_exits_0_and_opens_nothing(file_opens, capsys):
    assert r1.main(["--help"]) == 0
    assert file_opens == []
    assert "--real --r1-permit FILE --out DIR" in capsys.readouterr().out


def test_existing_output_folder_refuses_before_any_data_read(tmp_path, file_opens):
    out = tmp_path / "out"
    out.mkdir()
    permit = _permit(tmp_path)
    file_opens.clear()
    assert r1.main(["--real", "--r1-permit", str(permit), "--out", str(out)]) == 3
    assert file_opens == []


def test_unconfigured_real_paths_refuse_before_any_data_read(tmp_path, file_opens, monkeypatch):
    """With the pins and a trusted permit in place, an unset input path must still refuse before any read.
    The snapshot directory is redirected to a folder that does not exist, so a broken guard cannot touch real data."""
    monkeypatch.setattr(r1, "REAL_PINS", replace(r1.REAL_PINS, vocabulary_sha="b" * 64, d1_policy_sha="c" * 64))
    monkeypatch.setattr(r1, "REAL_SNAPSHOT_DIR", str(tmp_path / "no-snapshot-here"))
    permit = _permit(tmp_path)
    trust = _trusting(tmp_path, permit)
    file_opens.clear()
    with pytest.raises(r1.PermitRefused, match="not configured"):
        r1.load_real(permit, trust_path=trust)
    assert set(file_opens) <= {str(trust), str(permit), str(r1.BUILDER_PATH)}


@pytest.mark.parametrize("trust_value", ["0" * 64, "not-a-hash"])
def test_trust_mismatch_or_malformed_refuses(tmp_path, trust_value):
    permit = _permit(tmp_path)
    trust = _trust(tmp_path, trust_value)
    with pytest.raises(r1.PermitRefused):
        r1.load_real(permit, trust_path=trust)


def test_missing_permit_refuses(tmp_path):
    trust = _trust(tmp_path, "0" * 64)
    with pytest.raises(r1.PermitRefused, match="unreadable"):
        r1.load_real(tmp_path / "missing.json", trust_path=trust)


@pytest.mark.parametrize("case", [
    dict(raw=b"not json"),
    dict(remove=("date_utc",)),
    dict(extra={"note": "x"}),
    dict(stage="R2"),
    dict(owner_decision="yes"),
    dict(date_utc="2026-10-20"),
    dict(builder_sha256="0" * 64),
    dict(vocabulary_sha256="a" * 64),
    dict(d1_policy_sha256="a" * 64),
    dict(structural_receipt_manifest_sha256="0" * 64),
    dict(r0_receipt_sha256="xyz"),
    dict(stage=1),
])
def test_each_permit_field_is_checked(tmp_path, case):
    permit = _permit(tmp_path, **case)
    trust = _trusting(tmp_path, permit)
    with pytest.raises(r1.PermitRefused):
        r1.load_real(permit, trust_path=trust)


def test_builder_is_not_coupled_to_the_trust_file():
    assert r1.TRUST_PATH.name == "ieg_target_r1_trust.json"
    assert r1.TRUST_PATH != r1.BUILDER_PATH
    assert r1.sha256_bytes(_SCRIPT.read_bytes()) == r1.sha256_bytes(r1.BUILDER_PATH.read_bytes())


def test_subprocess_no_arguments_and_real_without_permit_exit_3(tmp_path):
    base = [sys.executable, str(_SCRIPT)]
    assert subprocess.run(base, capture_output=True).returncode == 3
    out = tmp_path / "out"
    proc = subprocess.run(base + ["--real", "--r1-permit", str(tmp_path / "none.json"), "--out", str(out)],
                          capture_output=True)
    assert proc.returncode == 3
    assert not out.exists()


def test_subprocess_help_exits_0():
    proc = subprocess.run([sys.executable, str(_SCRIPT), "--help"], capture_output=True, text=True)
    assert proc.returncode == 0 and "R1 target-only builder" in proc.stdout


# ---------------------------------------------------------------- section 5 and 11.8: determinism and stale locators

def _twelve_projects():
    projects = []
    for i in range(12):
        pid = f"P4{i:05d}"
        fy = 2010 + (i % 9)
        outcome = ["Satisfactory", "Unsatisfactory", "Highly Satisfactory", "Moderately Unsatisfactory"][i % 4]
        projects.append(_proj(pid, ["ARG", "BRA", "CHL"][i % 3], _rec(fy - 1, "Unsatisfactory"), _rec(fy, outcome),
                              _rec(fy, outcome)))
    return projects


def test_shuffled_snapshot_gives_byte_identical_analytic_outputs():
    base = H.make_world(_twelve_projects(), page_size=3)
    shuffled = H.make_world(_twelve_projects(), page_size=3, shuffle_seed=3)
    assert any(base.rows[i]["authoritative_records"] != shuffled.rows[i]["authoritative_records"]
               for i in range(len(base.rows))), "shuffle did not move any locator"
    res_a = H.run_pipeline(base)
    res_b = H.run_pipeline(shuffled)
    assert res_a.status == res_b.status == "ok"
    for name in ANALYTIC:
        assert res_a.files[name] == res_b.files[name], name
    assert res_a.input_sha256 != res_b.input_sha256


def test_stale_receipt_against_a_shuffled_snapshot_is_refused():
    base = H.make_world(_twelve_projects(), page_size=3)
    shuffled = H.make_world(_twelve_projects(), page_size=3, shuffle_seed=3)
    changed = [i for i in range(len(base.rows))
               if base.rows[i]["authoritative_records"] != shuffled.rows[i]["authoritative_records"]]
    assert changed, "shuffle did not move any locator"
    blobs = dict(shuffled.blobs)
    for key, value in base.blobs.items():
        if key.startswith("structural/") or key in (r1.K_VOCAB, r1.K_D1):
            blobs[key] = value
    pins = replace(shuffled.pins, structural_manifest_sha=base.pins.structural_manifest_sha)
    with pytest.raises(r1.IntegrityError, match="authoritative records"):
        r1.validate_bytes(blobs, pins)


# ---------------------------------------------------------------- section 2 and 10.10: forbidden keys

def test_forbidden_keys_never_reach_parsed_objects_or_outputs(tmp_path):
    world = H.make_world(ALL_PROJECTS)
    inputs = r1.validate_bytes(world.blobs, world.pins)
    rendered = repr(inputs)
    for marker in MARKERS:
        assert marker not in rendered
    res = H.run_pipeline(world)
    for name, data in res.files.items():
        for marker in MARKERS:
            assert marker.encode("ascii") not in data, (name, marker)
    final = _write(tmp_path / "out", res)
    for path in final.iterdir():
        for marker in MARKERS:
            assert marker.encode("ascii") not in path.read_bytes(), (path.name, marker)


def test_projection_hook_drops_non_whitelisted_keys_during_parsing():
    page = json.dumps({"count": 1, "data": [{"project_id": "P000001", "outcome": "Satisfactory",
                                              "bank_performance": H.FORBIDDEN_MARKERS["bank_performance"]}]})
    parsed = json.loads(page, object_pairs_hook=r1._projection_hook)
    assert set(parsed) == {"count", "data"}
    assert set(parsed["data"][0]) == {"project_id", "outcome"}
    assert H.FORBIDDEN_MARKERS["bank_performance"] not in repr(parsed)


# ---------------------------------------------------------------- section 12.4: receipts

def test_unknown_literal_bytes_never_contain_the_clear_text_in_blocked_receipt(tmp_path):
    projects = [_proj("P100001", "ARG", _rec(2012, "Something Unseen"))]
    _, res = _run(projects)
    assert res.status == "blocked"
    assert b"Something Unseen" not in res.files["blocked_receipt.json"]


# ---------------------------------------------------------------- sections 4 and 7.9: country weights, D1

def test_country_weights_and_d1_on_a_hand_example():
    projects = [
        _proj("P500001", "AAA", _rec(2010, "Satisfactory")),
        _proj("P500002", "AAA", _rec(2011, "Unsatisfactory")),
        _proj("P500003", "BBB", _rec(2012, "Satisfactory")),
        _proj("P500004", "CCC", _rec(2013, "Satisfactory")),
        _proj("P500005", "XXX", _rec(2021, "Satisfactory")),
        _proj("P500006", "YYY", _rec(2022, "Unsatisfactory")),
    ]
    d1 = {"cohort": "development", "min_G": 3, "max_largest_share": 0.5, "min_G_eff": 2.5}
    _, res = _run(projects, d1=d1)
    weights = _json(res, "country_weights.json")
    dev = weights["cohorts"]["development"]
    assert dev["G"] == 3 and dev["n_projects"] == 4
    assert dev["largest_country_share"] == pytest.approx(0.5)
    assert dev["G_eff"] == pytest.approx(1 / (0.5 ** 2 + 0.25 ** 2 + 0.25 ** 2))
    assert dev["countries"] == [["AAA", 2], ["BBB", 1], ["CCC", 1]]
    text = json.dumps(weights)
    assert "Satisfactory" not in text and "Unsatisfactory" not in text
    verdict = _json(res, "d1_verdict.json")
    assert verdict["policy_cohort_passes"] is True
    assert verdict["cohorts"]["holdout"]["G"] == 2


def test_d1_fails_when_g_eff_threshold_is_not_met():
    projects = [
        _proj("P500001", "AAA", _rec(2010, "Satisfactory")),
        _proj("P500002", "AAA", _rec(2011, "Satisfactory")),
        _proj("P500003", "BBB", _rec(2012, "Satisfactory")),
        _proj("P500004", "CCC", _rec(2013, "Satisfactory")),
    ]
    d1 = {"cohort": "development", "min_G": 3, "max_largest_share": 0.5, "min_G_eff": 3.0}
    _, res = _run(projects, d1=d1)
    assert _json(res, "d1_verdict.json")["policy_cohort_passes"] is False


def test_d1_on_an_empty_policy_cohort_fails_without_dividing_by_zero():
    _, res = _run([_proj("P500001", "AAA", _rec(2010, "Satisfactory"))],
                  d1={"cohort": "holdout", "min_G": 1, "max_largest_share": 1, "min_G_eff": 1})
    verdict = _json(res, "d1_verdict.json")
    assert verdict["policy_cohort_passes"] is False
    assert verdict["cohorts"]["holdout"]["largest_country_share"] is None


# ---------------------------------------------------------------- subprocess runs of the harness

def test_harness_cli_runs_twice_with_identical_analytic_outputs(tmp_path):
    for name in ("a", "b"):
        proc = subprocess.run([sys.executable, str(_HARNESS), "--out", str(tmp_path / name)],
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
    for name in ANALYTIC + ("target_audit.csv",):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes(), name
    man_a = json.loads((tmp_path / "a" / "manifest.json").read_text(encoding="utf-8"))
    man_b = json.loads((tmp_path / "b" / "manifest.json").read_text(encoding="utf-8"))
    man_a["run"].pop("utc_time")
    man_b["run"].pop("utc_time")
    assert man_a == man_b


def test_harness_blocked_scenario_exits_4_and_writes_only_the_receipt(tmp_path):
    proc = subprocess.run([sys.executable, str(_HARNESS), "--out", str(tmp_path / "x"), "--scenario", "blocked"],
                          capture_output=True, text=True)
    assert proc.returncode == 4, proc.stderr
    assert sorted(p.name for p in (tmp_path / "x.blocked").iterdir()) == ["blocked_receipt.json"]
    assert not (tmp_path / "x").exists()


# ---------------------------------------------------------------- guard against any research read

def test_the_research_root_guard_recognises_a_research_path():
    """The per-test guard in file_opens fails any test that opened a research path. Its match is checked here on a
    string; no research path is opened."""
    probe = "/Users/example/" + _FORBIDDEN_ROOT + "/ieg-data/x.json"
    assert _FORBIDDEN_ROOT in probe
    assert _FORBIDDEN_ROOT not in str(_SCRIPT)
