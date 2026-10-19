"""Offline tests for scripts/ieg_structural_eligibility.py (declaration v0.3, section 3).

Every fixture is synthetic. The pins are computed from the fixture files, so the
pin checks run exactly as they do on the real snapshot. No network is used.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import socket
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ieg_structural_eligibility.py"
_spec = importlib.util.spec_from_file_location("ieg_structural_eligibility", _SCRIPT)
eg = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = eg
_spec.loader.exec_module(eg)

TOOL = {
    "path": "scripts/ieg_structural_eligibility.py",
    "git_sha": "test",
    "git_dirty": False,
    "script_sha256": "test",
    "python": "test",
}
LABELS = {
    "outcome": "Satisfactory",
    "bank_performance": "Satisfactory",
    "me_quality": "Satisfactory",
    "quality_at_entry": "Satisfactory",
    "quality_of_supervision": "Satisfactory",
    "evaluation_type": "ICRR",
}
UNSET = object()
ABSENT = "ABSENT"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def R(pid, approval_fy=2000, eval_fy=2010, *, a_status="accepted", a_resolved=UNSET,
      a_rule="FY-01", a_reason="", c_status="resolved_agree", wb="ETH", c_rule="CW-01",
      c_reason="", pno="true", as_of="13-Oct-2026", eval_raw=UNSET, approval_raw=UNSET,
      labels=None):
    """One synthetic IEG record with its step C approval row and crosswalk row."""
    if a_resolved is UNSET:
        a_resolved = approval_fy if a_status != "quarantine" else ""
    ieg = {
        "project_id": pid,
        "approval_fy": approval_fy if approval_raw is UNSET else approval_raw,
        "evaluation_fy": str(eval_fy) if eval_raw is UNSET else eval_raw,
        "as_of_date": as_of,
    }
    ieg.update(LABELS if labels is None else labels)
    return {
        "ieg": ieg,
        "approval": {"status": a_status, "resolved": a_resolved, "rule_id": a_rule, "reason": a_reason},
        "country": {"status": c_status, "rule_id": c_rule, "reason": c_reason, "wb_code": wb, "pno_record_found": pno},
    }


def build_world(root: Path, rows, oghist_override=None, snapshot_pin_override=None):
    """Write the four synthetic inputs under root and return the pins that match them."""
    snap = root / "snapshot"
    appr = root / "approval"
    cw = root / "crosswalk"
    og = root / "oghist"
    for d in (snap / "ieg", appr, cw, og):
        d.mkdir(parents=True, exist_ok=True)

    # Snapshot: one IEG page with the whole fixture, label fields included.
    page_bytes = json.dumps({"count": len(rows), "data": [r["ieg"] for r in rows]},
                            ensure_ascii=False, sort_keys=True).encode("utf-8")
    (snap / "ieg" / "page-00000.json").write_bytes(page_bytes)
    page_sha = _sha(page_bytes)
    digest = _sha("".join(f"{page_sha}  ieg/page-00000.json\n" for _ in [0]).encode("utf-8"))
    snap_manifest = {
        "datasets": {"ieg": {"api_total": len(rows), "pages": [
            {"file": "ieg/page-00000.json", "sha256": page_sha, "records": len(rows)}]}},
        "snapshot_digest": digest,
        "snapshot_digest_method": "test fixture",
    }
    snap_bytes = json.dumps(snap_manifest, sort_keys=True, indent=1).encode("utf-8")
    (snap / "manifest.json").write_bytes(snap_bytes)
    snap_sha = _sha(snap_bytes)

    # Step C approval output.
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["project_id", "ieg_page_file", "ieg_index", "status", "resolved_wb_fy", "aux_state", "reason", "rule_id"])
    for i, r in enumerate(rows):
        a = r["approval"]
        w.writerow([r["ieg"]["project_id"], "ieg/page-00000.json", i, a["status"],
                    "" if a["resolved"] in ("", None) else a["resolved"], "agrees", a["reason"], a["rule_id"]])
    appr_rows = buf.getvalue().encode("utf-8")
    (appr / eg.APPROVAL_ROWS).write_bytes(appr_rows)
    appr_manifest = {
        "inputs": {"manifest_sha256": snap_sha, "page_digest": digest},
        "outputs": {eg.APPROVAL_ROWS: {"sha256": _sha(appr_rows), "bytes": len(appr_rows)}},
        "schema_version": "1",
    }
    appr_manifest_bytes = json.dumps(appr_manifest, sort_keys=True, indent=1).encode("utf-8")
    (appr / "manifest.json").write_bytes(appr_manifest_bytes)
    appr_sha = _sha(appr_manifest_bytes)

    # OGHIST r2: known cells for every (wb, FY 1989-2027) used, unless overridden.
    wbs = sorted({r["country"]["wb_code"] for r in rows if r["country"]["wb_code"]})
    overrides = oghist_override or {}
    og_buf = io.StringIO()
    ow = csv.writer(og_buf, lineterminator="\n")
    ow.writerow(["wb_code", "code_system", "iso3", "country_name", "effective_fy", "effective_start",
                 "effective_end", "income_group", "unknown_kind", "raw_cell_locator", "rule_id"])
    for wb in wbs:
        for fy in range(1989, 2028):
            if (wb, fy) in overrides:
                value = overrides[(wb, fy)]
                if value == ABSENT:
                    continue
                ow.writerow([wb, "WB", "", wb, fy, "", "", "", value, "x", "OGHIST-R1-GROUP"])
            else:
                ow.writerow([wb, "WB", "", wb, fy, "", "", "HIC", "", "x", "OGHIST-R1-GROUP"])
    og_rows = og_buf.getvalue().encode("utf-8")
    (og / eg.OGHIST_ROWS).write_bytes(og_rows)
    og_manifest = {"outputs": {eg.OGHIST_ROWS: {"sha256": _sha(og_rows), "bytes": len(og_rows)}}}
    og_manifest_bytes = json.dumps(og_manifest, sort_keys=True, indent=1).encode("utf-8")
    (og / "manifest.json").write_bytes(og_manifest_bytes)
    og_sha = _sha(og_manifest_bytes)

    # Step B crosswalk r3.
    cbuf = io.StringIO()
    cw_w = csv.writer(cbuf, lineterminator="\n")
    cw_w.writerow(["project_id", "ieg_page_file", "ieg_index", "status", "reason", "rule_id", "wb_code", "pno_record_found"])
    for i, r in enumerate(rows):
        c = r["country"]
        cw_w.writerow([r["ieg"]["project_id"], "ieg/page-00000.json", i, c["status"], c["reason"],
                       c["rule_id"], c["wb_code"], c["pno_record_found"]])
    cw_rows = cbuf.getvalue().encode("utf-8")
    (cw / eg.CROSSWALK_ROWS).write_bytes(cw_rows)
    cw_manifest = {
        "inputs": {
            "snapshot": {"manifest_sha256": snap_sha, "page_digest": digest},
            "approval": {"manifest_sha256": appr_sha, "rows_sha256": _sha(appr_rows)},
            "oghist": {"manifest_sha256": og_sha, "income_history_sha256": _sha(og_rows)},
        },
        "outputs": {eg.CROSSWALK_ROWS: {"sha256": _sha(cw_rows), "bytes": len(cw_rows)}},
    }
    cw_manifest_bytes = json.dumps(cw_manifest, sort_keys=True, indent=1).encode("utf-8")
    (cw / "manifest.json").write_bytes(cw_manifest_bytes)
    cw_sha = _sha(cw_manifest_bytes)

    pin_digest = digest if snapshot_pin_override is None else snapshot_pin_override
    return eg.Pins(
        snapshot_dir=str(snap), snapshot_manifest_sha=snap_sha, snapshot_page_digest=pin_digest,
        approval_dir=str(appr), approval_manifest_sha=appr_sha,
        crosswalk_dir=str(cw), crosswalk_manifest_sha=cw_sha,
        oghist_dir=str(og), oghist_manifest_sha=og_sha,
    )


def run_world(tmp_path: Path, rows, **kwargs):
    pins = build_world(tmp_path / "world", rows, **kwargs)
    out = tmp_path / "out"
    result = eg.run(pins, out, tool_info=TOOL)
    return pins, out, result


def read_candidates(out: Path) -> dict:
    with open(out / "structural_candidates.csv", newline="", encoding="utf-8") as fh:
        return {r["project_id"]: r for r in csv.DictReader(fh)}


def read_exclusions(out: Path) -> dict:
    """(page, index) -> (disposition, rule_id, detail). Each record appears once."""
    with open(out / "structural_exclusions.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    result = {}
    for r in rows:
        key = (r["ieg_page_file"], int(r["ieg_index"]))
        assert key not in result, f"record {key} listed twice"
        result[key] = (r["disposition"], r["rule_id"], r["detail"])
    return result


def loc(i: int) -> tuple[str, int]:
    return ("ieg/page-00000.json", i)


def qa_of(out: Path) -> dict:
    return json.loads((out / "structural_qa.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- eligibility rules

def test_accepted_record_is_a_candidate(tmp_path):
    _, out, _ = run_world(tmp_path, [R("P000001", 2000, 2010)])
    cands = read_candidates(out)
    assert set(cands) == {"P000001"}
    assert cands["P000001"]["window"] == "development"
    assert cands["P000001"]["wb_code"] == "ETH"
    assert read_exclusions(out) == {}


def test_approval_fy_1988_excluded_and_1989_kept(tmp_path):
    rows = [R("P000001", 1988, 2010), R("P000002", 1989, 2010)]
    _, out, _ = run_world(tmp_path, rows)
    exc = read_exclusions(out)
    assert exc[loc(0)][:2] == ("excluded", "A2")
    assert set(read_candidates(out)) == {"P000002"}


def test_evaluation_fy_window_boundaries(tmp_path):
    rows = [R("P000001", 2000, 2019), R("P000002", 2000, 2020), R("P000003", 2000, 2026), R("P000004", 2000, 2027)]
    _, out, _ = run_world(tmp_path, rows)
    cands = read_candidates(out)
    assert cands["P000001"]["window"] == "development"
    assert cands["P000002"]["window"] == "holdout"
    assert cands["P000003"]["window"] == "holdout"
    assert "P000004" not in cands
    assert read_exclusions(out)[loc(3)][:2] == ("excluded", "E2")


def test_invalid_evaluation_fy_is_excluded_and_integer_is_accepted(tmp_path):
    rows = [R("P000001", 2000, 2010, eval_raw="20x6"), R("P000002", 2000, 2015, eval_raw=2015)]
    _, out, _ = run_world(tmp_path, rows)
    assert read_exclusions(out)[loc(0)][:2] == ("excluded", "E0")
    assert read_candidates(out)["P000002"]["evaluation_fy"] == "2015"


@pytest.mark.parametrize("status", ["resolved_agree", "resolved_by_name", "resolved_by_code", "resolved_by_remap"])
def test_each_accepted_country_status_is_kept(tmp_path, status):
    rule = {"resolved_agree": "CW-01", "resolved_by_name": "CW-02", "resolved_by_code": "CW-03", "resolved_by_remap": "CW-08"}[status]
    _, out, _ = run_world(tmp_path, [R("P000001", c_status=status, c_rule=rule, wb="KEN")])
    cand = read_candidates(out)["P000001"]
    assert cand["wb_code"] == "KEN"
    assert cand["crosswalk_status"] == status


@pytest.mark.parametrize("status,code", [("excluded_regional", "C1"), ("conflict", "C2"), ("quarantine", "C3")])
def test_excluded_country_statuses_get_their_rule(tmp_path, status, code):
    _, out, _ = run_world(tmp_path, [R("P000001", c_status=status, c_rule="CW-05", wb="")])
    assert read_exclusions(out)[loc(0)][:2] == ("excluded", code)
    assert read_candidates(out) == {}


def test_unknown_country_status_refuses_the_run(tmp_path):
    with pytest.raises(eg.IntegrityError):
        run_world(tmp_path, [R("P000001", c_status="mystery", wb="")])


def test_step_c_quarantine_is_excluded(tmp_path):
    _, out, _ = run_world(tmp_path, [R("P000001", a_status="quarantine", a_resolved="", a_rule="FY-04", a_reason="date_ieg_fy_conflict")])
    assert read_exclusions(out)[loc(0)][:2] == ("excluded", "A1")


def test_fy_only_status_is_accepted(tmp_path):
    _, out, _ = run_world(tmp_path, [R("P000001", 2000, 2010, a_status="fy_only", a_rule="FY-03", a_reason="board_date_absent")])
    assert read_candidates(out)["P000001"]["approval_status"] == "fy_only"


def test_regional_excluded_when_approval_is_too_early(tmp_path):
    """Rule order: approval FY before 1989 is the primary reason, even with a regional country."""
    _, out, _ = run_world(tmp_path, [R("P000001", 1985, 2010, c_status="excluded_regional", c_rule="CW-05", wb="")])
    assert read_exclusions(out)[loc(0)][:2] == ("excluded", "A2")


@pytest.mark.parametrize("kind", ["dotdot", "blank"])
def test_unknown_oghist_cell_is_kept(tmp_path, kind):
    rows = [R("P000001", 2000, 2010, wb="ETH")]
    _, out, _ = run_world(tmp_path, rows, oghist_override={("ETH", 2000): kind})
    cand = read_candidates(out)["P000001"]
    assert cand["oghist_cell_presence"] == "unknown_kind"
    assert read_exclusions(out) == {}


def test_absent_oghist_cell_is_kept_and_reported(tmp_path):
    _, out, _ = run_world(tmp_path, [R("P000001", 2000, 2010, wb="ETH")], oghist_override={("ETH", 2000): ABSENT})
    assert read_candidates(out)["P000001"]["oghist_cell_presence"] == "absent"
    assert read_exclusions(out) == {}


def test_known_oghist_cell_is_reported_as_known(tmp_path):
    _, out, _ = run_world(tmp_path, [R("P000001", 2000, 2010, wb="ETH")])
    assert read_candidates(out)["P000001"]["oghist_cell_presence"] == "known"


def test_invalid_records_get_their_record_validity_reasons(tmp_path):
    rows = [
        R("X1", 2000, 2010),                      # V1: bad project id
        R("P000002", 2000, 2010, as_of="12-Oct-2026"),  # V2: as-of date
        R("P000003", approval_raw="2000", a_status="quarantine", a_resolved=""),  # V3: approval_fy not an integer
    ]
    _, out, _ = run_world(tmp_path, rows)
    exc = read_exclusions(out)
    assert exc[loc(0)][:2] == ("excluded", "V1")
    assert exc[loc(1)][:2] == ("excluded", "V2")
    assert exc[loc(2)][:2] == ("excluded", "V3")
    assert read_candidates(out) == {}


# ---------------------------------------------------------------- duplicates (rule 1)

def test_duplicate_takes_the_maximum_evaluation_fy(tmp_path):
    rows = [R("P000001", 2000, 2018), R("P000001", 2000, 2022)]
    _, out, _ = run_world(tmp_path, rows)
    cand = read_candidates(out)["P000001"]
    assert cand["evaluation_fy"] == "2022"
    assert cand["window"] == "holdout"
    assert cand["authoritative_records"] == "ieg/page-00000.json#1"
    assert cand["source_records"] == "ieg/page-00000.json#0;ieg/page-00000.json#1"
    assert read_exclusions(out)[loc(0)][:2] == ("superseded", "R1")


def test_equal_fy_equivalent_values_merge_into_one_row(tmp_path):
    rows = [R("P000001", 2012, 2022, wb="ETH"), R("P000001", 2012, 2022, wb="eth")]
    _, out, _ = run_world(tmp_path, rows)
    cand = read_candidates(out)["P000001"]
    assert cand["record_count"] == "2"
    assert cand["authoritative_records"] == "ieg/page-00000.json#0;ieg/page-00000.json#1"
    assert read_exclusions(out) == {}


def test_equal_fy_conflicting_approval_fy_excludes_the_project(tmp_path):
    rows = [R("P000001", 2010, 2022), R("P000001", 2011, 2022)]
    _, out, _ = run_world(tmp_path, rows)
    assert read_candidates(out) == {}
    exc = read_exclusions(out)
    assert exc[loc(0)][:2] == ("excluded", "D1")
    assert exc[loc(1)][:2] == ("excluded", "D1")


def test_equal_fy_conflicting_country_excludes_the_project(tmp_path):
    rows = [R("P000001", 2010, 2022, wb="ETH"), R("P000001", 2010, 2022, wb="KEN")]
    _, out, _ = run_world(tmp_path, rows)
    assert read_candidates(out) == {}
    assert read_exclusions(out)[loc(0)][:2] == ("excluded", "D1")


def test_regional_maximum_record_is_not_back_filled_from_a_lower_record(tmp_path):
    rows = [
        R("P000001", 2010, 2018, wb="ETH"),
        R("P000001", 2010, 2022, c_status="excluded_regional", c_rule="CW-05", wb=""),
    ]
    _, out, _ = run_world(tmp_path, rows)
    assert read_candidates(out) == {}
    exc = read_exclusions(out)
    assert exc[loc(1)][:2] == ("excluded", "C1")
    assert exc[loc(0)][:2] == ("superseded", "R1")


@pytest.mark.parametrize("order", ["ab", "ba"])
def test_tied_conflict_outcome_does_not_depend_on_record_order(tmp_path, order):
    a = R("P000001", 2010, 2022, wb="ETH")
    b = R("P000001", 2010, 2022, wb="KEN")
    rows = [a, b] if order == "ab" else [b, a]
    _, out, _ = run_world(tmp_path, rows)
    assert read_candidates(out) == {}


# ---------------------------------------------------------------- accounting and output

def test_every_record_is_counted_exactly_once(tmp_path):
    rows = [
        R("P000001", 2000, 2010),
        R("P000002", 1985, 2010),
        R("P000003", 2000, 2027),
        R("P000004", 2000, 2010, c_status="conflict", c_rule="CW-04", wb=""),
        R("P000005", 2000, 2018), R("P000005", 2000, 2024),
        R("P000006", 2010, 2022, wb="ETH"), R("P000006", 2011, 2022, wb="ETH"),
        R("X7", 2000, 2010),
    ]
    _, out, _ = run_world(tmp_path, rows)
    qa = qa_of(out)
    assert qa["conservation"] == {"accounted": len(rows), "ok": True, "records_in": len(rows)}
    exc = read_exclusions(out)
    cands = read_candidates(out)
    auth = set()
    for c in cands.values():
        auth.update(tuple([x.split("#")[0], int(x.split("#")[1])]) for x in c["authoritative_records"].split(";"))
    assert auth.isdisjoint(exc)
    assert len(auth) + len(exc) == len(rows)
    assert qa["records_in"] == len(rows)


def test_statement_and_structural_count_are_reported(tmp_path):
    _, out, _ = run_world(tmp_path, [R("P000001", 2000, 2010), R("P000002", 2000, 2021)])
    qa = qa_of(out)
    assert qa["statement"] == "structural candidate N, not rated-eligible N"
    assert qa["structural_candidate_N"] == 2
    assert qa["candidates_by_window"] == {"development": 1, "holdout": 1, "total": 2}


def test_split_files_are_sorted_with_trailing_newline_and_disjoint(tmp_path):
    rows = [R("P000002", 2000, 2010), R("P000001", 2000, 2021), R("P000003", 2000, 2019)]
    _, out, _ = run_world(tmp_path, rows)
    dev = (out / "split_ids_development.txt").read_text(encoding="utf-8")
    hold = (out / "split_ids_holdout.txt").read_text(encoding="utf-8")
    assert dev == "P000002\nP000003\n"
    assert hold == "P000001\n"
    assert set(dev.split()).isdisjoint(hold.split())


def test_output_is_byte_identical_across_runs(tmp_path):
    rows = [R("P000001", 2000, 2010), R("P000001", 2000, 2019, wb="KEN"), R("P000002", 1985, 2010), R("P000003", 2000, 2024)]
    pins = build_world(tmp_path / "world", rows)
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    eg.run(pins, out_a, tool_info=TOOL)
    eg.run(pins, out_b, tool_info=TOOL)
    names = sorted(p.name for p in out_a.iterdir())
    assert names == sorted(p.name for p in out_b.iterdir())
    for name in names:
        assert (out_a / name).read_bytes() == (out_b / name).read_bytes(), name


def test_manifest_hashes_every_output(tmp_path):
    _, out, result = run_world(tmp_path, [R("P000001", 2000, 2010)])
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    for name, entry in manifest["outputs"].items():
        assert _sha((out / name).read_bytes()) == entry["sha256"], name
    assert "split_policy.json" in manifest["outputs"]
    assert manifest["tool"] == TOOL
    assert _sha((out / "manifest.json").read_bytes()) == result["manifest_sha256"]


def test_output_directory_is_never_overwritten(tmp_path):
    pins = build_world(tmp_path / "world", [R("P000001", 2000, 2010)])
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(FileExistsError):
        eg.run(pins, out, tool_info=TOOL)
    partial = tmp_path / "other.partial"
    partial.mkdir()
    with pytest.raises(FileExistsError):
        eg.run(pins, tmp_path / "other", tool_info=TOOL)


# ---------------------------------------------------------------- labels are never read

def test_projection_keeps_only_the_whitelist(tmp_path):
    raw = json.dumps({"count": 1, "data": [{
        "project_id": "P000001", "approval_fy": 2000, "evaluation_fy": "2010",
        "as_of_date": "13-Oct-2026", "outcome": "Satisfactory", "bank_performance": "High",
        "me_quality": "Not Rated", "quality_at_entry": "Unsatisfactory",
        "quality_of_supervision": "Satisfactory", "evaluation_type": "ICRR"}]})
    obj = json.loads(raw, object_pairs_hook=eg._projection_hook)
    assert set(obj["data"][0]) == set(eg.WHITELIST)
    assert set(obj) == {"count", "data"}


def test_changing_rating_fields_leaves_every_decision_output_identical(tmp_path):
    base = [R("P000001", 2000, 2010), R("P000002", 2000, 2024, wb="KEN"), R("P000003", 1985, 2010)]
    changed_labels = {
        "outcome": "Unsatisfactory", "bank_performance": "Highly Unsatisfactory", "me_quality": "Highly Satisfactory",
        "quality_at_entry": "Unsatisfactory", "quality_of_supervision": "Highly Unsatisfactory", "evaluation_type": "PPAR",
    }
    changed = [R(r_pid, r_ap, r_ev, wb=wb, labels=changed_labels) for r_pid, r_ap, r_ev, wb in
               [("P000001", 2000, 2010, "ETH"), ("P000002", 2000, 2024, "KEN"), ("P000003", 1985, 2010, "ETH")]]
    _, out_a, _ = run_world(tmp_path / "a", base)
    _, out_b, _ = run_world(tmp_path / "b", changed)
    for name in ("structural_candidates.csv", "structural_exclusions.csv", "split_ids_development.txt",
                 "split_ids_holdout.txt", "structural_qa.json"):
        assert (out_a / name).read_bytes() == (out_b / name).read_bytes(), name
    # split_policy.json carries the input pins, and the snapshot pin changes when the page bytes do.
    # Everything except the pins is identical.
    pa = json.loads((out_a / "split_policy.json").read_text(encoding="utf-8"))
    pb = json.loads((out_b / "split_policy.json").read_text(encoding="utf-8"))
    pa.pop("inputs")
    pb.pop("inputs")
    assert pa == pb
    # The manifest records the snapshot pins and the output hashes. The approval, crosswalk and
    # OGHIST row hashes are label-free and must match.
    ma = json.loads((out_a / "manifest.json").read_text(encoding="utf-8"))
    mb = json.loads((out_b / "manifest.json").read_text(encoding="utf-8"))
    for name in ("structural_candidates.csv", "structural_exclusions.csv", "structural_qa.json"):
        assert ma["outputs"][name] == mb["outputs"][name], name
    assert ma["inputs"]["approval"]["rows_sha256"] == mb["inputs"]["approval"]["rows_sha256"]
    assert ma["inputs"]["crosswalk"]["rows_sha256"] == mb["inputs"]["crosswalk"]["rows_sha256"]
    assert ma["inputs"]["oghist"] == mb["inputs"]["oghist"]


# ---------------------------------------------------------------- no network

def test_run_makes_no_network_calls(tmp_path, monkeypatch):
    def _refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    _, out, _ = run_world(tmp_path, [R("P000001", 2000, 2010)])
    assert (out / "structural_qa.json").exists()


# ---------------------------------------------------------------- pins (each input refused)

def _mutate(path: Path):
    path.write_bytes(path.read_bytes() + b"\n")


@pytest.mark.parametrize("target", [
    "snapshot_manifest", "snapshot_page", "approval_manifest", "approval_rows",
    "crosswalk_manifest", "crosswalk_rows", "oghist_manifest", "oghist_rows",
])
def test_pin_mismatch_is_refused_for_each_input(tmp_path, target):
    rows = [R("P000001", 2000, 2010)]
    pins = build_world(tmp_path / "world", rows)
    paths = {
        "snapshot_manifest": Path(pins.snapshot_dir) / "manifest.json",
        "snapshot_page": Path(pins.snapshot_dir) / "ieg" / "page-00000.json",
        "approval_manifest": Path(pins.approval_dir) / "manifest.json",
        "approval_rows": Path(pins.approval_dir) / eg.APPROVAL_ROWS,
        "crosswalk_manifest": Path(pins.crosswalk_dir) / "manifest.json",
        "crosswalk_rows": Path(pins.crosswalk_dir) / eg.CROSSWALK_ROWS,
        "oghist_manifest": Path(pins.oghist_dir) / "manifest.json",
        "oghist_rows": Path(pins.oghist_dir) / eg.OGHIST_ROWS,
    }
    _mutate(paths[target])
    with pytest.raises(eg.PinRefused):
        eg.run(pins, tmp_path / "out", tool_info=TOOL)
    assert not (tmp_path / "out").exists()


def test_snapshot_digest_pin_mismatch_is_refused(tmp_path):
    rows = [R("P000001", 2000, 2010)]
    pins = build_world(tmp_path / "world", rows, snapshot_pin_override="0" * 64)
    with pytest.raises(eg.PinRefused):
        eg.run(pins, tmp_path / "out", tool_info=TOOL)


def test_step_c_resolved_fy_must_match_the_ieg_approval_fy(tmp_path):
    rows = [R("P000001", 2000, 2010, a_resolved=2001)]
    with pytest.raises(eg.IntegrityError):
        run_world(tmp_path, rows)


def test_inputs_must_cover_the_same_records(tmp_path):
    rows = [R("P000001", 2000, 2010)]
    pins = build_world(tmp_path / "world", rows)
    # Rebuild the approval output with an extra locator, then re-pin it: the coverage check must refuse.
    appr = Path(pins.approval_dir)
    extra = (appr / eg.APPROVAL_ROWS).read_bytes() + b"P000009,ieg/page-00000.json,5,accepted,2000,agrees,,FY-01\n"
    (appr / eg.APPROVAL_ROWS).write_bytes(extra)
    man = json.loads((appr / "manifest.json").read_text(encoding="utf-8"))
    man["outputs"][eg.APPROVAL_ROWS]["sha256"] = _sha(extra)
    man_bytes = json.dumps(man, sort_keys=True, indent=1).encode("utf-8")
    (appr / "manifest.json").write_bytes(man_bytes)
    pins = eg.Pins(**{**pins.__dict__, "approval_manifest_sha": _sha(man_bytes)})
    with pytest.raises((eg.PinRefused, eg.IntegrityError)):
        eg.run(pins, tmp_path / "out", tool_info=TOOL)


# ---------------------------------------------------------------- lineage between inputs

def _repin_manifest(directory: Path, mutate) -> str:
    """Rewrite a manifest after mutate(manifest) and return its new sha256."""
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    mutate(manifest)
    data = json.dumps(manifest, sort_keys=True, indent=1).encode("utf-8")
    path.write_bytes(data)
    return _sha(data)


@pytest.mark.parametrize("field", [
    ("snapshot", "manifest_sha256"),
    ("snapshot", "page_digest"),
    ("approval", "manifest_sha256"),
    ("approval", "rows_sha256"),
    ("oghist", "manifest_sha256"),
    ("oghist", "income_history_sha256"),
])
def test_crosswalk_lineage_is_refused(tmp_path, field):
    rows = [R("P000001", 2000, 2010)]
    pins = build_world(tmp_path / "world", rows)
    cw_dir = Path(pins.crosswalk_dir)

    def mutate(m):
        m["inputs"][field[0]][field[1]] = "0" * 64

    new_sha = _repin_manifest(cw_dir, mutate)
    pins = eg.Pins(**{**pins.__dict__, "crosswalk_manifest_sha": new_sha})
    with pytest.raises(eg.PinRefused):
        eg.run(pins, tmp_path / "out", tool_info=TOOL)


def test_approval_lineage_to_the_snapshot_is_refused(tmp_path):
    rows = [R("P000001", 2000, 2010)]
    pins = build_world(tmp_path / "world", rows)
    appr_dir = Path(pins.approval_dir)
    new_sha = _repin_manifest(appr_dir, lambda m: m["inputs"].__setitem__("manifest_sha256", "0" * 64))
    # Re-pin the crosswalk to the new approval manifest, so only the approval lineage can refuse.
    new_cw = _repin_manifest(Path(pins.crosswalk_dir),
                             lambda m: m["inputs"]["approval"].__setitem__("manifest_sha256", new_sha))
    pins = eg.Pins(**{**pins.__dict__, "approval_manifest_sha": new_sha, "crosswalk_manifest_sha": new_cw})
    with pytest.raises(eg.PinRefused, match="approval: built from a different snapshot manifest"):
        eg.run(pins, tmp_path / "out", tool_info=TOOL)
