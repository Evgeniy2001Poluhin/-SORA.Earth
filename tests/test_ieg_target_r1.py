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

# Open analytic files: development only (13.3). Holdout content is sealed (13.1, 13.2).
ANALYTIC = (
    "target_rows.csv",
    "target_exclusions.csv",
    "final_ids_development.txt",
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


def _columns(result, name):
    return next(csv.reader(io.StringIO(result.files[name].decode("utf-8"), newline="")))


def _json(result, name):
    return json.loads(result.files[name].decode("utf-8"))


SYNTH_PERMIT = H.SYNTH_PERMIT
HOLDOUT_NAMES = {
    "inner_sha256.txt", "target_rows_holdout.csv", "target_exclusions_holdout.csv",
    "target_audit_holdout.csv", "final_ids_holdout.txt", "country_weights_holdout.json",
    "d1_holdout_full.json", "qa_holdout.json",
}


def _open_holdout(res, world):
    return r1.open_seal(res.sealed[r1.SEALED_HOLDOUT], r1.PURPOSE_HOLDOUT, world.private_key, SYNTH_PERMIT)


def _diag(res, world):
    """The decrypted blocked diagnostics, as a dict. Owner-side only; the test holds the private key."""
    plain = r1.open_seal(res.sealed[r1.SEALED_BLOCKED], r1.PURPOSE_BLOCKED, world.private_key, SYNTH_PERMIT)
    return json.loads(r1.read_package(plain)["blocked_detail.json"].decode("utf-8"))


def _write_tree(final):
    return {p.relative_to(final).as_posix(): p.read_bytes() for p in final.rglob("*") if p.is_file()}


def _write(out, result):
    return r1.write_outputs(result, out)


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


def test_unknown_literal_blocks_and_stays_out_of_the_open_receipt(tmp_path):
    world, res = _run([_proj("P100001", "ARG", _rec(2012, "Excellent")), _proj("P100002", "ARG", _rec(2012, "Satisfactory"))])
    assert res.status == "blocked"
    assert set(res.files) == {"blocked_receipt.json"}
    receipt = _json(res, "blocked_receipt.json")
    assert receipt["status"] == "blocked" and receipt["reason"] == "unknown_outcome_values"
    assert "unknown_count" not in receipt and "unknown_values" not in receipt
    assert b"Excellent" not in res.files["blocked_receipt.json"]
    diag = _diag(res, world)
    literal_sha = hashlib.sha256(b'["string","Excellent"]').hexdigest()
    assert diag["unknown_count"] == 1
    assert diag["unknown_values"] == [{"canonical": ["string", "Excellent"], "count": 1}]


def test_literal_case_is_not_normalised():
    world, res = _run([_proj("P100001", "ARG", _rec(2012, "satisfactory"))])
    assert res.status == "blocked"
    assert b'"satisfactory"' not in res.files["blocked_receipt.json"]
    literal_sha = hashlib.sha256(b'["string","satisfactory"]').hexdigest()
    assert _diag(res, world)["unknown_values"][0]["canonical"] == ["string", "satisfactory"]


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
    world_str, res_str = _run([_proj("P100001", "ARG", _rec(2012, "NULL"))])
    assert res_str.status == "blocked"
    assert _diag(res_str, world_str)["unknown_values"][0]["canonical"][0] == "string"
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
    world, res = _run(projects)
    assert res.status == "blocked"
    diag = _diag(res, world)
    assert diag["unknown_count"] == 5
    assert {tuple(v["canonical"]) for v in diag["unknown_values"]} == {
        ("nonstring", "bool"), ("nonstring", "int"), ("nonstring", "float"),
        ("nonstring", "list"), ("nonstring", "object")}


def test_true_and_one_are_different_values():
    world, res = _run([_proj("P100001", "ARG", _rec(2012, True)), _proj("P100002", "ARG", _rec(2012, 1))])
    assert res.status == "blocked"
    assert _diag(res, world)["unknown_count"] == 2


def test_unknown_blocks_even_when_the_project_is_T4():
    world, res = _run([_proj("P100001", "ARG", _rec(2018, "Foo"), _rec(2018, "Satisfactory"))])
    assert res.status == "blocked"
    diag = _diag(res, world)
    assert diag["projects_by_rule"]["T4"] == 1
    assert diag["unknown_count"] == 1


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
    assert set(_columns(res, "target_rows.csv")) == {
        "project_id", "split", "ieg_outcome_satisfactory", "outcome_raw_canonical", "n_source_records"}
    assert set(_columns(res, "target_exclusions.csv")) == {"project_id", "split", "rule", "reason", "literals"}
    # The audit carries no country, FY or other feature: its column set is closed.
    assert set(_columns(res, "target_audit.csv")) == {
        "project_id", "ieg_page_file", "ieg_index", "outcome_present",
        "outcome_original_json", "outcome_canonical", "used"}
    qa = _json(res, "target_qa.json")
    assert qa["candidates"] == {"development": 4, "holdout": 3, "total": 7}
    assert qa["development"]["class_counts"] == {"1": 2, "0": 2}
    # 13.3: the open qa holds the holdout candidate count and nothing else about the holdout.
    assert "holdout" not in qa and "class_counts" not in qa
    assert all(r["split"] == "development" for r in _csv(res, "target_rows.csv"))
    assert all(r["split"] == "development" for r in _csv(res, "target_exclusions.csv"))


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
    tree = _write_tree(final)
    assert sorted(tree) == ["blocked_receipt.json", "manifest.json", r1.SEALED_BLOCKED]
    assert not (tmp_path / "out").exists() and not (tmp_path / "out.partial").exists()
    for name, blob in tree.items():
        for proj in projects:
            assert proj["pid"].encode("ascii") not in blob, name
        assert b"Excellent" not in blob, name


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


# ---------------------------------------------------------------- sections 6, 13.5, 13.9, 13.11.3: chain, trust, exit 3

def test_shipped_trust_file_holds_six_nulls():
    assert json.loads((_REPO / "scripts" / "ieg_target_r1_trust.json").read_text(encoding="utf-8")) == {
        key: None for key in r1.TRUST_KEYS}


def test_real_mode_refuses_with_the_shipped_trust_and_opens_only_the_trust(tmp_path, file_opens):
    permit = tmp_path / "permit.json"
    permit.write_bytes(b"{}")
    out = tmp_path / "out"
    file_opens.clear()
    code = r1.main(["--real", "--r1-permit", str(permit), "--out", str(out)])
    assert code == 3
    assert not out.exists()
    assert set(file_opens) <= {str(r1.TRUST_PATH)}


def test_real_chain_end_to_end_on_a_synthetic_tree(tmp_path, monkeypatch, file_opens):
    fx = H.build_real_fixture(tmp_path / "tree")
    fx.install(monkeypatch)
    code = r1.main(["--real", "--r1-permit", str(fx.permit), "--out", str(fx.paths["out"])])
    assert code == 0
    manifest = json.loads((fx.paths["out"] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run"]["mode"] == "real"
    assert manifest["run"]["permit_sha256"] == H.sha(fx.permit.read_bytes())
    assert manifest["run"]["core_dumps_disabled"] is True
    for key in ("chain/r0_receipt", "chain/r1a_procedure", "chain/vocabulary_approval", "chain/r1a_receipt",
                "chain/r1a_result"):
        assert key in manifest["inputs"]
    blob = (fx.paths["out"] / r1.SEALED_HOLDOUT).read_bytes()
    plain = r1.open_seal(blob, r1.PURPOSE_HOLDOUT, fx.owner, fx.permit.read_bytes())
    assert len(plain) == H.SYNTH_CAPACITY
    assert set(r1.read_package(plain)) == HOLDOUT_NAMES


def _tamper_after_build(doc, suffix=b" "):
    def hook(fx):
        path = fx.paths[doc]
        path.write_bytes(path.read_bytes() + suffix)
    return hook


def _pin_override(monkeypatch_attr, value):
    def hook(fx, mp):
        mp.setattr(r1, monkeypatch_attr, value)
    return hook


CHAIN_DEFECTS = [
    # id, overrides for build_real_fixture, hook after build, hook after install, expected message
    ("trust-permit-hash", {"trust": {"r1_permit_sha256": "0" * 64}}, None, None, "permit against the trust file"),
    ("trust-null-path", {"trust": {"r1a_result_path": None}}, None, None, "trust file holds null"),
    ("trust-extra-key", {"trust": {"note": "x"}}, None, None, "six chain keys"),
    ("r0-bytes-changed", {}, _tamper_after_build("r0"), None, "R0 receipt bytes"),
    ("r0-snapshot-manifest-pin", {"r0": {"snapshot_manifest_sha256": "0" * 64}}, None, None,
     "R0 snapshot manifest pin"),
    ("r0-snapshot-digest-pin", {"r0": {"snapshot_page_digest": "0" * 64}}, None, None,
     "R0 snapshot page digest pin"),
    ("r0-structural-pin", {"r0": {"structural_receipt_manifest_sha256": "0" * 64}}, None, None,
     "R0 structural pin"),
    ("r0-d1-pin", {"r0": {"d1_policy_sha256": "0" * 64}}, None, None, "R0 D1 pin"),
    ("r0-owner-key-not-the-pinned-one", {"r0": {"owner_x25519_public_key_hex": "1" * 64}}, None, None,
     "R0 owner public key pin"),
    ("r0-owner-key-unset", {}, None, _pin_override("REAL_OWNER_PUBLIC_KEY_HEX", None),
     "owner public key pin: no pin is set"),
    ("r0-owner-decision", {"r0": {"owner_decision": "да"}}, None, None, "stage or owner decision"),
    ("r0-procedure-hash", {"r0": {"r1a_procedure_sha256": "0" * 64}}, None, None, "R1a procedure bytes"),
    ("procedure-bytes-changed", {}, _tamper_after_build("procedure"), None, "R1a procedure bytes"),
    ("approval-link-to-r0", {"approval": {"r0_receipt_sha256": "0" * 64}}, None, None, "approval link to R0"),
    ("approval-vocabulary", {"approval": {"vocabulary_sha256": "0" * 64}}, None, None, "approval vocabulary"),
    ("approval-owner-decision", {"approval": {"owner_decision": "ок"}}, None, None,
     "vocabulary approval owner decision"),
    ("r1a-link-to-r0", {"r1a": {"r0_receipt_sha256": "0" * 64}}, None, None, "R1a link to R0"),
    ("r1a-link-to-procedure", {"r1a": {"r1a_procedure_sha256": "0" * 64}}, None, None, "R1a link to the procedure"),
    ("r1a-snapshot-digest", {"r1a": {"snapshot_page_digest": "0" * 64}}, None, None,
     "R1a snapshot page digest pin"),
    ("r1a-script-hash", {"r1a": {"r1a_script_sha256": "0" * 64}}, None, None, "R1a script against the procedure"),
    ("r1a-result-kind", {"r1a": {"result_kind": "literals_with_counts"}}, None, None,
     "R1a result kind against the procedure"),
    ("r1a-result-bytes-changed", {}, _tamper_after_build("r1a_result", b"x"), None, "R1a result bytes"),
    ("r1a-receipt-bytes-changed", {}, _tamper_after_build("r1a"), None, "R1a receipt bytes"),
    ("permit-stage", {"permit": {"stage": "R2"}}, None, None, "permit stage"),
    ("permit-owner-decision", {"permit": {"owner_decision": "yes"}}, None, None, "owner_decision"),
    ("permit-date", {"permit": {"date_utc": "2026-10-20"}}, None, None, "date_utc"),
    ("permit-builder-hash", {"permit": {"builder_sha256": "0" * 64}}, None, None, "permit builder_sha256"),
    ("permit-vocabulary-hash", {"permit": {"vocabulary_sha256": "0" * 64}}, None, None, "vocabulary"),
    ("permit-capacity-not-int", {"permit": {"sealed_plaintext_bytes": "4194304"}}, None, None,
     "sealed_plaintext_bytes"),
    ("permit-capacity-bool", {"permit": {"sealed_plaintext_bytes": True}}, None, None,
     "sealed_plaintext_bytes"),
    ("permit-outcomes", {"permit": {"allowed_public_outcomes": {"status": ["ok"], "holdout_d1": ["pass"]}}},
     None, None, "allowed_public_outcomes"),
    ("permit-extra-field", {"permit": {"note": "x"}}, None, None, "unexpected"),
    ("permit-missing-field", {"permit": {"date_utc": H.REMOVE}}, None, None, "missing"),
    ("permit-structural-hash", {"permit": {"structural_receipt_manifest_sha256": "0" * 64}}, None, None,
     "permit structural pin"),
    ("vocabulary-file-changed", {}, lambda fx: fx.paths["vocabulary"].write_bytes(b"{}"), None, "vocabulary pin"),
    ("paths-not-configured", {}, None, _pin_override("REAL_VOCABULARY_PATH", None), "not configured"),
]


@pytest.mark.parametrize("case", CHAIN_DEFECTS, ids=[c[0] for c in CHAIN_DEFECTS])
def test_each_chain_defect_refuses_before_any_page_is_read(tmp_path, monkeypatch, file_opens, case):
    _, overrides, after_build, after_install, expected = case
    fx = H.build_real_fixture(tmp_path / "tree", overrides=overrides)
    if after_build is not None:
        after_build(fx)
    fx.install(monkeypatch)
    if after_install is not None:
        after_install(fx, monkeypatch)
    file_opens.clear()
    with pytest.raises(r1.PinRefused, match=expected):
        r1.load_real(fx.permit, trust_path=fx.trust)
    touched = [p for p in file_opens if str(fx.paths["snapshot"]) in p or str(fx.paths["structural"]) in p]
    assert touched == [], "a snapshot or structural file was read before the chain passed"


def test_missing_trust_and_missing_permit_refuse(tmp_path, monkeypatch):
    fx = H.build_real_fixture(tmp_path / "tree")
    fx.install(monkeypatch)
    with pytest.raises(r1.PermitRefused, match="trust file unreadable"):
        r1.load_real(fx.permit, trust_path=tmp_path / "missing-trust.json")
    with pytest.raises(r1.PermitRefused, match="permit unreadable"):
        r1.load_real(tmp_path / "missing-permit.json", trust_path=fx.trust)


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
    permit = tmp_path / "permit.json"
    permit.write_bytes(b"{}")
    file_opens.clear()
    assert r1.main(["--real", "--r1-permit", str(permit), "--out", str(out)]) == 3
    assert file_opens == []


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
    for name, data in _write_tree(final).items():
        for marker in MARKERS:
            assert marker.encode("ascii") not in data, (name, marker)


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
    dev = weights["cohort"]
    assert dev["G"] == 3 and dev["n_projects"] == 4
    assert dev["largest_country_share"] == pytest.approx(0.5)
    assert dev["G_eff"] == pytest.approx(1 / (0.5 ** 2 + 0.25 ** 2 + 0.25 ** 2))
    assert dev["countries"] == [["AAA", 2], ["BBB", 1], ["CCC", 1]]
    text = json.dumps(weights)
    assert "Satisfactory" not in text and "Unsatisfactory" not in text and "XXX" not in text
    verdict = _json(res, "d1_verdict.json")
    assert verdict["development"]["passes"] is True
    # 13.1: the open verdict gives the holdout one word, and no holdout figure.
    assert verdict["holdout_d1"] == "fail"
    assert "holdout" not in verdict and "cohorts" not in verdict
    assert "XXX" not in json.dumps(verdict) and "YYY" not in json.dumps(verdict)


def test_d1_fails_when_g_eff_threshold_is_not_met():
    projects = [
        _proj("P500001", "AAA", _rec(2010, "Satisfactory")),
        _proj("P500002", "AAA", _rec(2011, "Satisfactory")),
        _proj("P500003", "BBB", _rec(2012, "Satisfactory")),
        _proj("P500004", "CCC", _rec(2013, "Satisfactory")),
    ]
    d1 = {"cohort": "development", "min_G": 3, "max_largest_share": 0.5, "min_G_eff": 3.0}
    _, res = _run(projects, d1=d1)
    assert _json(res, "d1_verdict.json")["development"]["passes"] is False


def test_d1_on_an_empty_policy_cohort_fails_without_dividing_by_zero():
    _, res = _run([_proj("P500001", "AAA", _rec(2010, "Satisfactory"))],
                  d1={"cohort": "holdout", "min_G": 1, "max_largest_share": 1, "min_G_eff": 1})
    verdict = _json(res, "d1_verdict.json")
    assert verdict["holdout_d1"] == "fail"
    assert "holdout" not in verdict and "cohorts" not in verdict


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
    # The ciphertext (fresh randomness, 13.2) is the only output that may differ.
    assert man_a["outputs"].pop(r1.SEALED_HOLDOUT)["sha256"] != man_b["outputs"].pop(r1.SEALED_HOLDOUT)["sha256"]
    assert man_a == man_b


def test_harness_blocked_scenario_exits_4_and_writes_only_the_receipt(tmp_path):
    proc = subprocess.run([sys.executable, str(_HARNESS), "--out", str(tmp_path / "x"), "--scenario", "blocked"],
                          capture_output=True, text=True)
    assert proc.returncode == 4, proc.stderr
    assert sorted(_write_tree(tmp_path / "x.blocked")) == ["blocked_receipt.json", "manifest.json",
                                                           r1.SEALED_BLOCKED]
    assert not (tmp_path / "x").exists()



# ---------------------------------------------------------------- section 13.2 and 13.9: sealed packages

def _seal_with(world, **changes):
    return replace(world.seal, **changes)


def _run_with_seal(world, seal):
    inputs = r1.validate_bytes(world.blobs, world.pins)
    inputs = replace(inputs, run_meta=r1.make_run_meta("synthetic", git=False), seal=seal)
    return r1.build(inputs)


def test_holdout_package_holds_the_eight_holdout_files_and_nothing_open():
    world, res = _run(ALL_PROJECTS)
    assert res.status == "ok"
    assert set(res.sealed) == {r1.SEALED_HOLDOUT}
    files = r1.read_package(_open_holdout(res, world))
    assert set(files) == HOLDOUT_NAMES
    assert b"P000005" in files["target_rows_holdout.csv"]
    assert b"P000005" not in b"".join(res.files.values())


def test_plaintext_is_padded_to_the_permit_capacity():
    world, res = _run(ALL_PROJECTS)
    assert len(_open_holdout(res, world)) == world.seal.capacity
    blocked_world, blocked = _run(ALL_PROJECTS + [_proj("P999111", "PER", _rec(2024, "Excellent"))])
    plain = r1.open_seal(blocked.sealed[r1.SEALED_BLOCKED], r1.PURPOSE_BLOCKED, blocked_world.private_key,
                         SYNTH_PERMIT)
    assert len(plain) == blocked_world.seal.capacity


def test_ciphertext_is_fresh_on_every_seal():
    world, first = _run(ALL_PROJECTS)
    second = H.run_pipeline(world)
    a, b = first.sealed[r1.SEALED_HOLDOUT], second.sealed[r1.SEALED_HOLDOUT]
    assert a != b
    assert a[14:46] != b[14:46], "ephemeral public key repeated"
    assert a[46:58] != b[46:58], "nonce repeated"


def test_ciphertext_matches_the_specified_construction():
    """Re-derives the key from 13.9 and decrypts with the header as AAD; a mutant that drops the AAD fails here."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import serialization

    world, res = _run(ALL_PROJECTS)
    blob = res.sealed[r1.SEALED_HOLDOUT]
    header, body = blob[:58], blob[58:]
    assert header[:12] == b"SORA-R1-SEAL" and header[12] == 1 and header[13] == 1
    eph_pub, nonce = header[14:46], header[46:58]
    public = world.private_key.public_key().public_bytes(encoding=serialization.Encoding.Raw,
                                                         format=serialization.PublicFormat.Raw)
    shared = world.private_key.exchange(X25519PublicKey.from_public_bytes(eph_pub))
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=hashlib.sha256(SYNTH_PERMIT).digest(),
               info=b"SORA-R1-SEAL v1" + bytes([1]) + public).derive(shared)
    plain = AESGCM(key).decrypt(nonce, body, header)
    assert plain == _open_holdout(res, world)


@pytest.mark.parametrize("offset", [0, 12, 13, 14, 46, 58, -1])
def test_any_header_or_tag_byte_change_refuses(offset):
    world, res = _run(ALL_PROJECTS)
    blob = bytearray(res.sealed[r1.SEALED_HOLDOUT])
    blob[offset] ^= 0x01
    with pytest.raises(r1.IntegrityError):
        r1.open_seal(bytes(blob), r1.PURPOSE_HOLDOUT, world.private_key, SYNTH_PERMIT)


def test_wrong_purpose_wrong_key_and_wrong_permit_refuse():
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    world, res = _run(ALL_PROJECTS)
    blob = res.sealed[r1.SEALED_HOLDOUT]
    with pytest.raises(r1.IntegrityError):
        r1.open_seal(blob, r1.PURPOSE_BLOCKED, world.private_key, SYNTH_PERMIT)
    with pytest.raises(r1.IntegrityError):
        r1.open_seal(blob, r1.PURPOSE_HOLDOUT, X25519PrivateKey.generate(), SYNTH_PERMIT)
    with pytest.raises(r1.IntegrityError):
        r1.open_seal(blob, r1.PURPOSE_HOLDOUT, world.private_key, b"another permit")


def test_overflowing_the_capacity_is_exit_5_and_writes_nothing(tmp_path):
    world = H.make_world(ALL_PROJECTS)
    with pytest.raises(r1.IntegrityError, match="field_limit_exceeded"):
        _run_with_seal(world, _seal_with(world, capacity=1000))



def test_holdout_plaintext_is_never_written_to_disk(tmp_path):
    """The write guard scopes build() to no writes and write_outputs() to the output folders, and fails any
    holdout marker written inside those scopes. Every write API is covered (see test_guard_catches_each_api)."""
    world, res = _run(ALL_PROJECTS)
    final = _write(tmp_path / "out", res)
    for name, data in _write_tree(final).items():
        for marker in HOLDOUT_MARKERS:
            assert marker not in data, (name, marker)


def test_manifest_holds_only_the_ciphertext_hash(tmp_path):
    world, res = _run(ALL_PROJECTS)
    final = _write(tmp_path / "out", res)
    manifest = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
    names = set(manifest["outputs"])
    assert r1.SEALED_HOLDOUT in names
    assert not (HOLDOUT_NAMES & names)
    cipher = (final / r1.SEALED_HOLDOUT).read_bytes()
    assert manifest["outputs"][r1.SEALED_HOLDOUT]["sha256"] == hashlib.sha256(cipher).hexdigest()



def test_blocked_diagnostics_are_sealed_and_not_split_by_window():
    projects = ALL_PROJECTS + [_proj("P999111", "PER", _rec(2024, "Excellent"))]
    world, res = _run(projects)
    assert res.status == "blocked"
    diag = _diag(res, world)
    assert set(diag) == {"statement", "rule_version", "projects_by_rule", "unknown_count", "unknown_values"}
    text = json.dumps(diag)
    assert "development" not in text and "holdout" not in text
    assert diag["unknown_count"] == 1



def test_blocked_manifest_names_the_digest_of_the_sealed_diagnostics(tmp_path):
    world, res = _run([_proj("P100001", "ARG", _rec(2012, "Excellent"))])
    final = _write(tmp_path / "out", res)
    manifest = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["outputs"][r1.SEALED_BLOCKED]
    assert entry["sha256"] == hashlib.sha256(res.sealed[r1.SEALED_BLOCKED]).hexdigest()
    assert entry["bytes"] == len(res.sealed[r1.SEALED_BLOCKED])
    assert manifest["run"]["mode"] == "synthetic"
    receipt = _json(res, "blocked_receipt.json")
    assert set(receipt) == {"status", "reason", "statement", "rule_version", "pins", "inputs_sha256"}


# ---------------------------------------------------------------- sections 13.11-13.13: ceilings, bound, USTAR

def test_ceilings_and_tar_record_size_are_the_documented_values():
    assert (r1.ROW_MAX, r1.JSON_MAX, r1.INNER_BYTES, r1.TAR_RECORDSIZE) == (16384, 1048576, 65, 10240)


def _accepted_literal(literal, fy):
    """True if the run is ok; False if it refuses with field_limit_exceeded; any other outcome fails."""
    vocab = {"map": dict(H.DEFAULT_VOCAB["map"], **{literal: 1}), "not_rated": ["Not Rated"]}
    world = H.make_world([_proj("P100001", "ARG", _rec(fy, literal))], vocab=vocab)
    try:
        res = H.run_pipeline(world)
    except r1.IntegrityError as exc:
        assert str(exc) == "field_limit_exceeded"
        return False
    return res.status == "ok"


@pytest.mark.parametrize("fy", [2012, 2021], ids=["development", "holdout"])
def test_row_max_boundary_is_exact(fy):
    """The largest accepted literal is the one where some serialized record reaches ROW_MAX; one more byte refuses."""
    assert _accepted_literal("L", fy)
    lo, hi = 1, 20000
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _accepted_literal("L" * mid, fy):
            lo = mid
        else:
            hi = mid - 1
    assert _accepted_literal("L" * lo, fy)
    assert not _accepted_literal("L" * (lo + 1), fy)
    assert 1000 < lo < 16384


def test_a_256_control_character_literal_passes():
    literal = "".join(chr(1 + i % 8) for i in range(256))
    assert _accepted_literal(literal, 2012)


def test_json_max_boundary_is_exact(monkeypatch):
    projects = [_proj(f"P1{i:05d}", "ARG", _rec(2012, f"Unknown {i}")) for i in range(400)]
    monkeypatch.setattr(r1, "JSON_MAX", 10 ** 9)
    world, res = _run(projects)
    plain = r1.open_seal(res.sealed[r1.SEALED_BLOCKED], r1.PURPOSE_BLOCKED, world.private_key, SYNTH_PERMIT)
    size = len(r1.read_package(plain)["blocked_detail.json"])
    # The open receipt is also a JSON file under the same ceiling (it lists every input hash), so the
    # exact boundary is the larger of the two files.
    limit = max(size, len(res.files["blocked_receipt.json"]))
    monkeypatch.setattr(r1, "JSON_MAX", limit)
    assert H.run_pipeline(H.make_world(projects)).status == "blocked"
    monkeypatch.setattr(r1, "JSON_MAX", limit - 1)
    with pytest.raises(r1.IntegrityError, match="field_limit_exceeded"):
        H.run_pipeline(H.make_world(projects))


def test_a_name_over_100_bytes_or_not_ascii_is_a_field_limit():
    inner = {"inner_sha256.txt": b"x" * 65}
    r1._tar_bytes({**inner, "x" * 100: b"a"})
    with pytest.raises(r1.IntegrityError, match="field_limit_exceeded"):
        r1._tar_bytes({**inner, "x" * 101: b"a"})
    with pytest.raises(r1.IntegrityError, match="field_limit_exceeded"):
        r1._tar_bytes({**inner, "ü.csv": b"a"})


def test_bound_covers_archives_whose_files_are_all_at_their_ceilings():
    holdout_h, max_m = 3, 2
    counts = {"holdout": holdout_h, "max_records": max_m}
    holdout_sizes = {
        "inner_sha256.txt": 65,
        "target_rows_holdout.csv": (holdout_h + 1) * r1.ROW_MAX,
        "target_exclusions_holdout.csv": (holdout_h + 1) * r1.ROW_MAX,
        "target_audit_holdout.csv": (holdout_h * max_m + 1) * r1.ROW_MAX,
        "final_ids_holdout.txt": 8 * holdout_h,
        "country_weights_holdout.json": r1.JSON_MAX,
        "d1_holdout_full.json": r1.JSON_MAX,
        "qa_holdout.json": r1.JSON_MAX,
    }
    tar = r1._tar_bytes({n: b"x" * s for n, s in holdout_sizes.items()})
    assert len(tar) <= r1.sealed_upper_bound(counts)
    blocked = r1._tar_bytes({"inner_sha256.txt": b"x" * 65, "blocked_detail.json": b"x" * r1.JSON_MAX})
    assert len(blocked) <= r1.sealed_upper_bound(counts)
    assert len(tar) % r1.TAR_RECORDSIZE == 0


def test_capacity_below_the_bound_refuses_before_any_page(tmp_path, monkeypatch, file_opens):
    fx = H.build_real_fixture(tmp_path / "tree", overrides={"permit": {"sealed_plaintext_bytes": 1000}})
    fx.install(monkeypatch)
    file_opens.clear()
    with pytest.raises(r1.PermitRefused, match="below the bound"):
        r1.load_real(fx.permit, trust_path=fx.trust)
    assert not any(str(fx.paths["snapshot"]) in p for p in file_opens)


# ---------------------------------------------------------------- sections 13.6 and 13.9: pairs and determinism

DEV_COMMON = [
    _proj("P000001", "ARG", _rec(2012, "Satisfactory")),
    _proj("P000002", "ARG", _rec(2015, "Unsatisfactory")),
    _proj("P000003", "BRA", _rec(2016, "Highly Satisfactory")),
    _proj("P000004", "CHL", _rec(2018, "Moderately Unsatisfactory")),
]
# Two holdout contents with the same candidate count: different classes, T2 and T4 in the second.
HOLD_A = [
    _proj("P000005", "BRA", _rec(2021, "Satisfactory")),
    _proj("P000006", "CHL", _rec(2023, "Highly Unsatisfactory")),
]
HOLD_B = [
    _proj("P000005", "ARG", _rec(2021, "Not Rated")),
    _proj("P000006", "ARG", _rec(2023, "Satisfactory"), _rec(2023, "Unsatisfactory")),
]


def _open_outputs(res):
    return {k: v for k, v in res.files.items() if k not in ("manifest.json", "d1_verdict.json")}


def test_open_outputs_do_not_depend_on_the_holdout_contents():
    _, res_a = _run(DEV_COMMON + HOLD_A)
    world_b, res_b = _run(DEV_COMMON + HOLD_B)
    assert res_a.status == res_b.status == "ok"
    assert _open_outputs(res_a) == _open_outputs(res_b)
    verdict_a, verdict_b = _json(res_a, "d1_verdict.json"), _json(res_b, "d1_verdict.json")
    assert {k: v for k, v in verdict_a.items() if k != "holdout_d1"} == {
        k: v for k, v in verdict_b.items() if k != "holdout_d1"}
    # The inputs differ, and that is the only place the two runs may differ outside the sealed package.
    assert res_a.input_sha256 != res_b.input_sha256



# 13.9: the only fields of blocked_receipt.json that may differ between runs are the named input hashes.
INPUT_HASH_PINS = ("snapshot_manifest_sha256", "snapshot_page_digest", "structural_receipt_manifest_sha256",
                   "vocabulary_sha256", "d1_policy_sha256")


def _receipt_bytes_without_input_hashes(res):
    receipt = _json(res, "blocked_receipt.json")
    assert set(receipt) == {"status", "reason", "statement", "rule_version", "pins", "inputs_sha256"}
    receipt.pop("inputs_sha256")
    for key in INPUT_HASH_PINS:
        receipt["pins"].pop(key)
    assert receipt["pins"] == {}
    return r1._json_bytes(receipt)


def test_blocked_receipt_does_not_say_which_window_holds_the_unknown_value():
    unknown_in_holdout = DEV_COMMON + [_proj("P000005", "ARG", _rec(2021, "Excellent"))]
    unknown_in_dev = [_proj("P000001", "ARG", _rec(2012, "Excellent"))] + DEV_COMMON[1:] + [
        _proj("P000005", "ARG", _rec(2021, "Satisfactory"))]
    _, res_h = _run(unknown_in_holdout)
    _, res_d = _run(unknown_in_dev)
    assert res_h.status == res_d.status == "blocked"
    assert _receipt_bytes_without_input_hashes(res_h) == _receipt_bytes_without_input_hashes(res_d)


def test_audit_content_is_the_same_after_a_reordering_though_locators_move():
    base = H.make_world(_twelve_projects(), page_size=3)
    shuffled = H.make_world(_twelve_projects(), page_size=3, shuffle_seed=3)
    res_a = H.run_pipeline(base)
    res_b = H.run_pipeline(shuffled)

    def content(res):
        return sorted((r["project_id"], r["outcome_present"], r["outcome_original_json"],
                       r["outcome_canonical"], r["used"]) for r in _csv(res, "target_audit.csv"))

    assert content(res_a) == content(res_b)
    assert _csv(res_a, "target_audit.csv") != _csv(res_b, "target_audit.csv")


def test_decrypted_plaintext_is_identical_across_runs_though_ciphertexts_differ():
    world, first = _run(ALL_PROJECTS)
    second = H.run_pipeline(world)
    assert first.sealed[r1.SEALED_HOLDOUT] != second.sealed[r1.SEALED_HOLDOUT]
    assert _open_holdout(first, world) == _open_holdout(second, world)


def test_plaintext_is_exactly_the_expected_tar_then_zero_padding():
    import tarfile

    world, res = _run(ALL_PROJECTS)
    plain = _open_holdout(res, world)
    files = r1.read_package(plain)
    expected = r1._tar_bytes(files)
    assert plain[:len(expected)] == expected
    assert plain[len(expected):] == b"\0" * (len(plain) - len(expected))
    assert len(plain) == world.seal.capacity
    with tarfile.open(fileobj=io.BytesIO(plain), mode="r:") as tar:
        names = tar.getnames()
    assert names == ["inner_sha256.txt"] + sorted(n for n in HOLDOUT_NAMES if n != "inner_sha256.txt")


# ---------------------------------------------------------------- review fixes: write guard, core dumps, stderr, diagnostics

import contextlib
import resource
import subprocess
import tempfile

HOLDOUT_MARKERS = (b"P000005", b"P000006", b"P000007", b"P000008", b"Excellent", b"ustar")
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
_CURRENT_GUARD = None


class _WriteGuard:
    """Watches the whole pipeline: loading, validation, build and writing, from the test's start.

    Every scope names the folders its writes may go to (() means none). A write, a file creation or an
    os.write on a descriptor the pipeline did not open inside an allowed folder is a violation. Violations
    are kept in `violations`, which the fixture checks at teardown, so a violation swallowed by main()'s
    error handling still fails the test. Holdout markers inside the bytes written in a scope are violations."""

    def __init__(self):
        self.stack = []
        self.buffer = []
        self.fd_paths = {}
        self.violations = []

    def _violate(self, message):
        self.violations.append(message)
        raise AssertionError(message)

    def check_path(self, path, action):
        if not self.stack:
            return
        real = os.path.realpath(os.fsdecode(path))
        if not any(real == root or real.startswith(root + os.sep) for root in self.stack[-1]):
            self._violate(f"write guard: {action} outside the pipeline's folders")

    def record(self, path, data):
        if not self.stack:
            return
        if path is None:
            self._violate("write guard: write to a descriptor the pipeline did not open")
        self.check_path(path, "write")
        self.buffer.append(data.encode("utf-8") if isinstance(data, str) else bytes(data))

    @contextlib.contextmanager
    def scope(self, roots):
        outermost = not self.stack
        self.stack.append([os.path.realpath(os.fsdecode(r)) for r in roots])
        try:
            yield
        finally:
            self.stack.pop()
        if outermost:
            joined = b"".join(self.buffer)
            self.buffer = []
            if any(marker in joined for marker in HOLDOUT_MARKERS):
                self._violate("write guard: a holdout marker reached a write")


class _Handle:
    """Proxy for a write-mode file object: every write goes through the guard first."""

    def __init__(self, handle, path, guard):
        self._handle, self._path, self._guard = handle, path, guard

    def write(self, data):
        self._guard.record(self._path, data)
        return self._handle.write(data)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def __enter__(self):
        self._handle.__enter__()
        return self

    def __exit__(self, *exc):
        return self._handle.__exit__(*exc)

    def __iter__(self):
        return iter(self._handle)

    def __getattr__(self, name):
        return getattr(self._handle, name)


@pytest.fixture(autouse=True)
def write_guard(monkeypatch):
    """Patches open/io.open, os.open/os.write/os.close, the tempfile creators, and the pipeline steps
    load_real, validate_bytes, build and write_outputs. Installed before each test body."""
    global _CURRENT_GUARD
    guard = _WriteGuard()
    _CURRENT_GUARD = guard
    real_open, real_io_open = builtins.open, io.open
    real_os_open, real_os_write, real_os_close = os.open, os.write, os.close
    real_named, real_mkstemp, real_tmpfile = tempfile.NamedTemporaryFile, tempfile.mkstemp, tempfile.TemporaryFile

    def _open(file, mode="r", *args, **kwargs):
        writing = any(c in mode for c in "wax+")
        if isinstance(file, int):
            handle = real_io_open(file, mode, *args, **kwargs)
            return _Handle(handle, guard.fd_paths.get(file), guard) if writing else handle
        if writing:
            guard.check_path(file, "open for writing")
            handle = real_open(file, mode, *args, **kwargs)
            guard.fd_paths[handle.fileno()] = file
            return _Handle(handle, file, guard)
        return real_open(file, mode, *args, **kwargs)

    def _os_open(path, flags, *args, **kwargs):
        if flags & _WRITE_FLAGS:
            guard.check_path(path, "create")
            fd = real_os_open(path, flags, *args, **kwargs)
            guard.fd_paths[fd] = path
            return fd
        return real_os_open(path, flags, *args, **kwargs)

    def _os_write(fd, data):
        if guard.stack:
            guard.record(guard.fd_paths.get(fd), data)
        return real_os_write(fd, data)

    def _os_close(fd):
        guard.fd_paths.pop(fd, None)
        return real_os_close(fd)

    def _named(*args, **kwargs):
        guard.check_path(kwargs.get("dir") or tempfile.gettempdir(), "temporary file")
        return real_named(*args, **kwargs)

    def _mkstemp(*args, **kwargs):
        guard.check_path(kwargs.get("dir") or tempfile.gettempdir(), "temporary file")
        return real_mkstemp(*args, **kwargs)

    def _tmpfile(*args, **kwargs):
        if guard.stack:
            guard._violate("write guard: an unnamed temporary file inside the pipeline")
        return real_tmpfile(*args, **kwargs)

    def _step(fn, roots_for):
        def scoped(*args, **kwargs):
            with guard.scope(roots_for(*args, **kwargs)):
                return fn(*args, **kwargs)
        return scoped

    real_load, real_validate = r1.load_real, r1.validate_bytes
    real_build, real_write = r1.build, r1.write_outputs
    monkeypatch.setattr(builtins, "open", _open)
    monkeypatch.setattr(io, "open", _open)
    monkeypatch.setattr(os, "open", _os_open)
    monkeypatch.setattr(os, "write", _os_write)
    monkeypatch.setattr(os, "close", _os_close)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", _named)
    monkeypatch.setattr(tempfile, "mkstemp", _mkstemp)
    monkeypatch.setattr(tempfile, "TemporaryFile", _tmpfile)
    monkeypatch.setattr(r1, "load_real", _step(real_load, lambda *a, **k: ()))
    monkeypatch.setattr(r1, "validate_bytes", _step(real_validate, lambda *a, **k: ()))
    monkeypatch.setattr(r1, "build", _step(real_build, lambda *a, **k: ()))
    monkeypatch.setattr(r1, "write_outputs", _step(real_write, lambda result, out: r1.output_paths(out)))
    yield guard
    _CURRENT_GUARD = None
    assert not guard.violations, guard.violations


@pytest.fixture(autouse=True)
def core_limit_stubbed(monkeypatch):
    """Every test runs with setrlimit replaced by a recorder, so no test lowers pytest's own core limit.
    The real (0, 0) call is checked in a child process (test_disable_core_dumps_in_a_child_process)."""
    calls = []
    monkeypatch.setattr(resource, "setrlimit", lambda which, limits: calls.append((which, limits)))
    return calls


def test_guard_refuses_a_write_outside_the_scope(tmp_path):
    target = tmp_path / "leak.bin"
    with pytest.raises(AssertionError, match="write guard"):
        with _CURRENT_GUARD.scope([tmp_path / "out"]):
            open(target, "wb").write(b"x")
    _CURRENT_GUARD.violations.clear()
    assert not target.exists()


def test_guard_refuses_a_holdout_marker_inside_the_scope(tmp_path):
    with pytest.raises(AssertionError, match="holdout marker"):
        with _CURRENT_GUARD.scope([tmp_path]):
            (tmp_path / "a.bin").write_bytes(b"P000005")
    _CURRENT_GUARD.violations.clear()


def test_guard_refuses_os_write_on_a_descriptor_the_pipeline_did_not_open(tmp_path):
    with pytest.raises(AssertionError, match="descriptor"):
        with _CURRENT_GUARD.scope([tmp_path]):
            os.write(1, b"P000005\n")
    _CURRENT_GUARD.violations.clear()


@pytest.mark.parametrize("api", ["open", "write_text", "tempfile", "mkstemp", "exception"])
def test_guard_catches_each_api(tmp_path, api):
    """A write to an unallowed folder through each API, and on an exception path, is refused."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    def leaky():
        if api == "open":
            open(elsewhere / "x.bin", "wb").write(b"ustar")
        elif api == "write_text":
            Path(elsewhere / "x.txt").write_text("P000005")
        elif api == "tempfile":
            tempfile.NamedTemporaryFile(dir=elsewhere, delete=False).close()
        elif api == "mkstemp":
            fd, _ = tempfile.mkstemp(dir=elsewhere)
            os.close(fd)
        else:
            try:
                raise r1.IntegrityError(r1.FIELD_LIMIT)
            except r1.IntegrityError:
                open(elsewhere / "x.bin", "wb").write(b"ustar")
                raise

    with pytest.raises(AssertionError, match="write guard"):
        with _CURRENT_GUARD.scope([tmp_path / "out"]):
            leaky()
    _CURRENT_GUARD.violations.clear()


def test_disable_core_dumps_sets_zero_in_a_child_process():
    """The real call, in a child process, so pytest's own limits are not touched."""
    code = (
        "import importlib.util, resource, sys\n"
        f"spec = importlib.util.spec_from_file_location('r1', {str(_SCRIPT)!r})\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "sys.modules['r1'] = m\n"
        "spec.loader.exec_module(m)\n"
        "result = m.disable_core_dumps()\n"
        "print(result, resource.getrlimit(resource.RLIMIT_CORE))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "True (0, 0)"


def test_core_dumps_are_disabled_before_the_first_page_is_read(tmp_path, monkeypatch, core_limit_stubbed):
    fx = H.build_real_fixture(tmp_path / "tree")
    fx.install(monkeypatch)
    events = []
    real_core = r1.disable_core_dumps
    real_pages = r1._read_real_snapshot
    monkeypatch.setattr(r1, "disable_core_dumps", lambda: events.append("core") or real_core())
    monkeypatch.setattr(r1, "_read_real_snapshot", lambda blobs: events.append("pages") or real_pages(blobs))
    r1.load_real(fx.permit, trust_path=fx.trust)
    assert events == ["core", "pages"]
    assert core_limit_stubbed == [(resource.RLIMIT_CORE, (0, 0))]


def test_refused_core_dump_switch_refuses_with_zero_data_opens(tmp_path, monkeypatch, file_opens, capsys):
    fx = H.build_real_fixture(tmp_path / "tree")
    fx.install(monkeypatch)

    def refuse(which, limits):
        raise OSError("setrlimit refused")

    monkeypatch.setattr(resource, "setrlimit", refuse)
    file_opens.clear()
    with pytest.raises(r1.PermitRefused, match="core dumps"):
        r1.load_real(fx.permit, trust_path=fx.trust)
    assert file_opens == []
    out = tmp_path / "out"
    assert r1.main(["--real", "--r1-permit", str(fx.permit), "--out", str(out)]) == 3
    assert file_opens == [] and not out.exists()
    assert capsys.readouterr().err.strip() == "refused"


def test_parse_error_carries_no_value_size_or_label():
    with pytest.raises(r1.IntegrityError) as info:
        r1._parse_json(b'{"count": ' + b"9" * 5000 + b"}", "ieg/page-00000.json")
    assert str(info.value) == "integrity_error"
    assert info.value.__suppress_context__


def test_loader_boundary_raises_fixed_messages_only(tmp_path, monkeypatch):
    """A page whose sha256 is wrong, and an unexpected internal error, both leave load_real with no detail."""
    fx = H.build_real_fixture(tmp_path / "tree")
    fx.install(monkeypatch)
    page = fx.paths["snapshot"] / "ieg" / "page-00000.json"
    page.write_bytes(page.read_bytes() + b" ")
    with pytest.raises(r1.PinRefused) as info:
        r1.load_real(fx.permit, trust_path=fx.trust)
    assert str(info.value) == "snapshot page sha256 does not match the manifest"
    assert page.name not in str(info.value)

    def broken(blobs):
        raise KeyError("P000005")

    monkeypatch.setattr(r1, "_read_real_snapshot", broken)
    with pytest.raises(r1.IntegrityError) as info:
        r1.load_real(fx.permit, trust_path=fx.trust)
    assert str(info.value) == "integrity_error" and "P000005" not in str(info.value)


def _malformed_run(tmp_path, monkeypatch, capsys, name, digits, parse_calls):
    """A synthetic real-mode tree whose holdout page carries a number of `digits` digits. The page's hashes and
    the chain are rebuilt around it, so the run reaches the page parser and no earlier check."""
    def hook(world):
        page = next(k for k, v in world.blobs.items()
                    if k.startswith("snapshot/ieg/") and b'"outcome": "Moderately Satisfactory"' in v)
        data = world.blobs[page].replace(b'"outcome": "Moderately Satisfactory"',
                                         b'"outcome": ' + digits.encode("ascii"))
        H.replace_page(world, page[len("snapshot/"):], data)

    fx = H.build_real_fixture(tmp_path / name, world_hook=hook)
    fx.install(monkeypatch)
    real_parse = r1._parse_json

    def spy(data, label, **kwargs):
        parse_calls.append(len(data))
        return real_parse(data, label, **kwargs)

    monkeypatch.setattr(r1, "_parse_json", spy)
    capsys.readouterr()
    code = r1.main(["--real", "--r1-permit", str(fx.permit), "--out", str(fx.paths["out"])])
    captured = capsys.readouterr()
    out = fx.paths["out"]
    return code, captured.out, captured.err, out.exists(), out.with_name("out.blocked").exists()


def test_malformed_holdout_values_reach_the_parser_and_give_identical_output(tmp_path, monkeypatch, capsys):
    calls_a, calls_b = [], []
    first = _malformed_run(tmp_path, monkeypatch, capsys, "a", "1" * 5000, calls_a)
    second = _malformed_run(tmp_path, monkeypatch, capsys, "b", "2" * 6000, calls_b)
    assert max(calls_a) > 5000 and max(calls_b) > 6000, "the page parser was not reached"
    assert first == second
    code, out, err, made, blocked = first
    assert code == 5 and out == "" and err.strip() == "integrity_error"
    assert not made and not blocked
    assert "5000" not in err and "page" not in err


def test_blocked_diagnostics_hold_the_unknown_literals_and_counts_across_both_windows():
    projects = [
        _proj("P100001", "ARG", _rec(2012, "Excellent")),
        _proj("P100002", "ARG", _rec(2012, "Satisfactory")),
        _proj("P700001", "ARG", _rec(2021, "Excellent")),
        _proj("P700002", "ARG", _rec(2022, "Foo")),
    ]
    world, res = _run(projects)
    assert res.status == "blocked"
    diag = _diag(res, world)
    assert diag["unknown_count"] == 3
    assert diag["unknown_values"] == [
        {"canonical": ["string", "Excellent"], "count": 2},
        {"canonical": ["string", "Foo"], "count": 1},
    ]
    assert b"Excellent" not in res.files["blocked_receipt.json"]


# ---------------------------------------------------------------- guard against any research read

def test_the_research_root_guard_recognises_a_research_path():
    """The per-test guard in file_opens fails any test that opened a research path. Its match is checked here on a
    string; no research path is opened."""
    probe = "/Users/example/" + _FORBIDDEN_ROOT + "/ieg-data/x.json"
    assert _FORBIDDEN_ROOT in probe
    assert _FORBIDDEN_ROOT not in str(_SCRIPT)
