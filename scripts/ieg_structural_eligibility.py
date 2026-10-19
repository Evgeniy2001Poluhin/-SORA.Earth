#!/usr/bin/env python3
"""Pre-registration structural eligibility for the SORA.Earth IEG pilot.

Declaration v0.3, section 3: the no-y structural check. Its N is a structural
candidate count. It is NOT the number of rated-eligible projects.

No label is read. Each IEG page is parsed with an object hook that keeps only
project_id, approval_fy, evaluation_fy and as_of_date (plus the page-level count
and data keys) and drops every other key while the object is built. The step C
approval output and the country crosswalk r3 output are read for their status,
resolution and rule columns only. The OGHIST r2 output is read for wb_code,
effective_fy and unknown_kind only; income_group is never accessed.

Usage: python scripts/ieg_structural_eligibility.py --out <NEW directory>
The output directory must not exist. It is built as <out>.partial and renamed.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

RULE_VERSION = "1"
STATEMENT = "structural candidate N, not rated-eligible N"
WHITELIST = ("project_id", "approval_fy", "evaluation_fy", "as_of_date")
PAGE_KEYS = ("count", "data")
SNAPSHOT_AS_OF = _dt.date(2026, 10, 13)
SNAPSHOT_AS_OF_RAW = "13-Oct-2026"
APPROVAL_FY_MIN = 1989
DEVELOPMENT_MAX_EVAL_FY = 2019
HOLDOUT_MIN_EVAL_FY = 2020
HOLDOUT_MAX_EVAL_FY = 2026
EVAL_FY_EXCLUDED_FROM = 2027
SMALL_COUNTRY_N = 5
APPROVAL_ACCEPTED = ("accepted", "accepted_with_aux_warning", "fy_only")
APPROVAL_QUARANTINE = "quarantine"
COUNTRY_ACCEPTED = ("resolved_agree", "resolved_by_name", "resolved_by_code", "resolved_by_remap")
COUNTRY_EXCLUDED_RULE = {
    "excluded_regional": "C1",
    "conflict": "C2",
    "quarantine": "C3",
}
PID_RE = re.compile(r"P[0-9]{6}")
EVAL_FY_RE = re.compile(r"[0-9]{4}")
AS_OF_RE = re.compile(r"([0-9]{2})-([A-Za-z]{3})-([0-9]{4})")
MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}

# Fixed, documented order. A record receives the FIRST reason in this list that
# applies to it, so every excluded record has exactly one primary reason.
# Record validity, then identity (R1), then approval (A), evaluation FY (E),
# country (C), and finally the used-value conflict among tied records (D1).
RULES = (
    ("V1", "project_id_invalid", "record validity: project_id matches P plus six digits"),
    ("V2", "as_of_date_invalid", "record validity: as_of_date parses and equals the snapshot as-of date 13-Oct-2026"),
    ("V3", "approval_fy_raw_invalid", "record validity: IEG approval_fy is a JSON integer, not a bool"),
    ("E0", "evaluation_fy_invalid", "record validity: evaluation_fy is a 4-digit string or an integer in 1000-9999"),
    ("R1", "superseded_lower_eval_fy", "identity: a project keeps only its records at the maximum evaluation FY; lower ones are superseded, not excluded"),
    ("A1", "approval_fy_quarantine", "approval: step C status quarantine"),
    ("A2", "approval_fy_before_1989", "approval: verified approval FY below 1989"),
    ("E2", "evaluation_fy_ge_2027", "evaluation: FY 2027 or later is excluded from both windows"),
    ("C1", "regional_or_multicountry", "country: crosswalk r3 excluded_regional"),
    ("C2", "country_conflict", "country: crosswalk r3 conflict"),
    ("C3", "country_quarantine", "country: crosswalk r3 quarantine"),
    ("D1", "duplicate_used_values_conflict", "identity: records tied at the maximum evaluation FY disagree on used values (approval FY, resolved country)"),
)
REASON_BY_RULE = {code: reason for code, reason, _ in RULES}
RULE_ORDER = {code: i for i, (code, _, _) in enumerate(RULES)}

ROOT = "/Users/evgenijpoluhin/sora-research"
APPROVAL_ROWS = "project_approval_no_y.csv"
CROSSWALK_ROWS = "project_country_no_y.csv"
OGHIST_ROWS = "income_history.csv"


class PinRefused(RuntimeError):
    """An input does not match its pinned hash. The run stops."""


class IntegrityError(RuntimeError):
    """An input is internally inconsistent or holds an unexpected value. The run stops."""


@dataclass(frozen=True)
class Pins:
    snapshot_dir: str
    snapshot_manifest_sha: str
    snapshot_page_digest: str
    approval_dir: str
    approval_manifest_sha: str
    crosswalk_dir: str
    crosswalk_manifest_sha: str
    oghist_dir: str
    oghist_manifest_sha: str


REAL_PINS = Pins(
    snapshot_dir=f"{ROOT}/worldbank-snapshots/2026-10-15-sorted",
    snapshot_manifest_sha="b3d43f8033367d79bb14ace692f8cada7068e98a3540fdfbbc0a1bea2505474c",
    snapshot_page_digest="960d776e8db25a59abcb3562e921c1643ace32466d71682c017b2912dc43d4f3",
    approval_dir=f"{ROOT}/ieg-data/approval-fy-2026-10-16",
    approval_manifest_sha="73c443f33707c238c88544bab10473d662c922647e07c13b9a6cd4970ff85619",
    crosswalk_dir=f"{ROOT}/ieg-data/country-crosswalk-2026-10-19-r3",
    crosswalk_manifest_sha="4142df2936d1fe7df1b5b06c7ac1819cefe7bc6e0a1dabe5392796e9df681b53",
    oghist_dir=f"{ROOT}/ieg-data/oghist-2026-10-16-r2",
    oghist_manifest_sha="385d6e35e7db73d60b8a0d8ae407d54a870e106723acd175e69fd656dae6a4c2",
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_pinned_manifest(directory: str, expected_sha: str, label: str) -> dict:
    raw = (Path(directory) / "manifest.json").read_bytes()
    got = sha256_bytes(raw)
    if got != expected_sha:
        raise PinRefused(f"{label}: manifest.json sha256 {got} does not match the pinned {expected_sha}")
    return json.loads(raw.decode("utf-8"))


def read_pinned_output(directory: str, manifest: dict, name: str, label: str) -> bytes:
    data = (Path(directory) / name).read_bytes()
    got = sha256_bytes(data)
    expected = manifest["outputs"][name]["sha256"]
    if got != expected:
        raise PinRefused(f"{label}: {name} sha256 {got} does not match its manifest {expected}")
    return data


def _projection_hook(pairs):
    """Keep only the whitelisted keys (and the page-level count/data keys) as each object is built."""
    return {k: v for k, v in pairs if k in WHITELIST or k in PAGE_KEYS}


@dataclass(frozen=True)
class SourceRecord:
    page: str
    index: int
    project_id: object
    approval_raw: object
    eval_raw: object
    as_of_raw: object


@dataclass(frozen=True)
class ApprovalRow:
    project_id: str
    status: str
    resolved: int | None
    rule_id: str
    reason: str


@dataclass(frozen=True)
class CountryRow:
    project_id: str
    status: str
    rule_id: str
    reason: str
    wb_code: str
    pno_record_found: str


def load_snapshot(pins: Pins) -> tuple[list[SourceRecord], dict]:
    base = Path(pins.snapshot_dir)
    manifest = read_pinned_manifest(pins.snapshot_dir, pins.snapshot_manifest_sha, "snapshot")
    lines = []
    for ds in sorted(manifest["datasets"]):
        for page in manifest["datasets"][ds]["pages"]:
            actual = sha256_bytes((base / page["file"]).read_bytes())
            if actual != page["sha256"]:
                raise PinRefused(f"snapshot: {page['file']} sha256 {actual} does not match the manifest {page['sha256']}")
            lines.append(f"{page['sha256']}  {page['file']}")
    digest = sha256_bytes("".join(line + "\n" for line in sorted(lines)).encode("utf-8"))
    if digest != manifest["snapshot_digest"] or digest != pins.snapshot_page_digest:
        raise PinRefused(f"snapshot: page digest {digest} does not match the pinned {pins.snapshot_page_digest}")

    ieg = manifest["datasets"]["ieg"]
    records: list[SourceRecord] = []
    page_shas: dict[str, str] = {}
    for page in ieg["pages"]:
        obj = json.loads((base / page["file"]).read_bytes().decode("utf-8"), object_pairs_hook=_projection_hook)
        data = obj["data"]
        if obj["count"] != ieg["api_total"] or len(data) != page["records"]:
            raise IntegrityError(f"snapshot: {page['file']} count {obj['count']} (api_total {ieg['api_total']}), data {len(data)}, manifest {page['records']}")
        page_shas[page["file"]] = page["sha256"]
        for index, item in enumerate(data):
            records.append(SourceRecord(
                page=page["file"],
                index=index,
                project_id=item.get("project_id"),
                approval_raw=item.get("approval_fy"),
                eval_raw=item.get("evaluation_fy"),
                as_of_raw=item.get("as_of_date"),
            ))
    if len(records) != ieg["api_total"]:
        raise IntegrityError(f"snapshot: {len(records)} records read, api_total is {ieg['api_total']}")
    return records, {"ieg_page_sha256": page_shas, "page_digest": digest}


def load_approval(pins: Pins) -> tuple[dict, str]:
    manifest = read_pinned_manifest(pins.approval_dir, pins.approval_manifest_sha, "approval")
    if manifest["inputs"]["manifest_sha256"] != pins.snapshot_manifest_sha:
        raise PinRefused("approval: built from a different snapshot manifest")
    if manifest["inputs"]["page_digest"] != pins.snapshot_page_digest:
        raise PinRefused("approval: built from a different snapshot page digest")
    data = read_pinned_output(pins.approval_dir, manifest, APPROVAL_ROWS, "approval")
    index: dict[tuple[str, int], ApprovalRow] = {}
    for row in csv.DictReader(io.StringIO(data.decode("utf-8"), newline="")):
        key = (row["ieg_page_file"], int(row["ieg_index"]))
        if key in index:
            raise IntegrityError(f"approval: duplicate locator {key}")
        index[key] = ApprovalRow(
            project_id=row["project_id"],
            status=row["status"],
            resolved=int(row["resolved_wb_fy"]) if row["resolved_wb_fy"] else None,
            rule_id=row["rule_id"],
            reason=row["reason"],
        )
    return index, sha256_bytes(data)


def load_crosswalk(pins: Pins, approval_rows_sha: str) -> tuple[dict, str, str]:
    manifest = read_pinned_manifest(pins.crosswalk_dir, pins.crosswalk_manifest_sha, "crosswalk")
    inputs = manifest["inputs"]
    if inputs["snapshot"]["manifest_sha256"] != pins.snapshot_manifest_sha or inputs["snapshot"]["page_digest"] != pins.snapshot_page_digest:
        raise PinRefused("crosswalk: built from a different snapshot")
    if inputs["approval"]["manifest_sha256"] != pins.approval_manifest_sha:
        raise PinRefused("crosswalk: built from a different approval manifest")
    if inputs["approval"]["rows_sha256"] != approval_rows_sha:
        raise PinRefused("crosswalk: built from different approval rows")
    if inputs["oghist"]["manifest_sha256"] != pins.oghist_manifest_sha:
        raise PinRefused("crosswalk: built from a different OGHIST manifest")
    data = read_pinned_output(pins.crosswalk_dir, manifest, CROSSWALK_ROWS, "crosswalk")
    index: dict[tuple[str, int], CountryRow] = {}
    for row in csv.DictReader(io.StringIO(data.decode("utf-8"), newline="")):
        key = (row["ieg_page_file"], int(row["ieg_index"]))
        if key in index:
            raise IntegrityError(f"crosswalk: duplicate locator {key}")
        index[key] = CountryRow(
            project_id=row["project_id"],
            status=row["status"],
            rule_id=row["rule_id"],
            reason=row["reason"],
            wb_code=row["wb_code"],
            pno_record_found=row["pno_record_found"],
        )
    return index, sha256_bytes(data), manifest["inputs"]["oghist"]["income_history_sha256"]


def load_oghist(pins: Pins) -> tuple[dict, str]:
    """(wb_code, effective_fy) -> unknown_kind, where '' means a known cell.

    Three columns are read. The income_group column is never accessed.
    """
    manifest = read_pinned_manifest(pins.oghist_dir, pins.oghist_manifest_sha, "oghist")
    data = read_pinned_output(pins.oghist_dir, manifest, OGHIST_ROWS, "oghist")
    reader = csv.reader(io.StringIO(data.decode("utf-8"), newline=""))
    header = next(reader)
    c_wb = header.index("wb_code")
    c_fy = header.index("effective_fy")
    c_unknown = header.index("unknown_kind")
    cells: dict[tuple[str, int], str] = {}
    for row in reader:
        key = (row[c_wb], int(row[c_fy]))
        if key in cells:
            raise IntegrityError(f"oghist: duplicate cell {key}")
        cells[key] = row[c_unknown]
    return cells, sha256_bytes(data)


def parse_as_of(raw) -> _dt.date | None:
    if not isinstance(raw, str):
        return None
    m = AS_OF_RE.fullmatch(raw)
    if not m or m.group(2).capitalize() not in MONTHS:
        return None
    try:
        return _dt.date(int(m.group(3)), MONTHS[m.group(2).capitalize()], int(m.group(1)))
    except ValueError:
        return None


def parse_eval_fy(raw) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and EVAL_FY_RE.fullmatch(raw):
        value = int(raw)
    else:
        return None
    return value if 1000 <= value <= 9999 else None


def is_json_int(raw) -> bool:
    return isinstance(raw, int) and not isinstance(raw, bool)


def locator_text(key: tuple[str, int]) -> str:
    return f"{key[0]}#{key[1]}"


def _used_values(approval_row: ApprovalRow, country_row: CountryRow) -> tuple[str, str]:
    """Canonical used values for the duplicate check: approval FY and resolved country."""
    fy = str(approval_row.resolved) if approval_row.status in APPROVAL_ACCEPTED else f"status:{approval_row.status}"
    wb = country_row.wb_code.strip().upper() if country_row.status in COUNTRY_ACCEPTED else f"status:{country_row.status}"
    return fy, wb


def _record_exclusion(key, eval_fy, approval, crosswalk):
    """Rules A1, A2, E2, C1-C3 for a record at its project's maximum FY. Returns (rule, detail) or None."""
    a = approval[key]
    if a.status == APPROVAL_QUARANTINE:
        return ("A1", f"step C reason={a.reason}")
    if a.status not in APPROVAL_ACCEPTED:
        raise IntegrityError(f"approval status {a.status!r} at {locator_text(key)} is not a known status")
    if a.resolved is None:
        raise IntegrityError(f"approval at {locator_text(key)} has status {a.status} but no resolved FY")
    if a.resolved < APPROVAL_FY_MIN:
        return ("A2", f"approval_fy={a.resolved}")
    if eval_fy >= EVAL_FY_EXCLUDED_FROM:
        return ("E2", f"evaluation_fy={eval_fy}")
    c = crosswalk[key]
    if c.status in COUNTRY_ACCEPTED:
        if not c.wb_code.strip():
            raise IntegrityError(f"crosswalk at {locator_text(key)} is {c.status} with no wb_code")
        return None
    if c.status in COUNTRY_EXCLUDED_RULE:
        return (COUNTRY_EXCLUDED_RULE[c.status], f"crosswalk={c.status} rule={c.rule_id} detail={c.reason}")
    raise IntegrityError(f"crosswalk status {c.status!r} at {locator_text(key)} is not a known status")


def decide(records, approval, crosswalk, oghist):
    """Apply the rules in fixed order. Returns (dispositions, project_status, candidates, valid_project_count)."""
    disposition: dict[tuple[str, int], tuple[str, str, str]] = {}
    groups: dict[str, list] = defaultdict(list)
    for rec in records:
        key = (rec.page, rec.index)
        failure = None
        eval_fy = None
        if not (isinstance(rec.project_id, str) and PID_RE.fullmatch(rec.project_id)):
            failure = ("V1", repr(rec.project_id))
        elif parse_as_of(rec.as_of_raw) != SNAPSHOT_AS_OF:
            failure = ("V2", repr(rec.as_of_raw))
        elif not is_json_int(rec.approval_raw):
            failure = ("V3", repr(rec.approval_raw))
        else:
            eval_fy = parse_eval_fy(rec.eval_raw)
            if eval_fy is None:
                failure = ("E0", repr(rec.eval_raw))
        if failure is not None:
            disposition[key] = ("excluded", failure[0], failure[1])
            continue
        groups[rec.project_id].append((key, eval_fy))

    project_status: dict[str, tuple[str, str]] = {}
    candidates: list[dict] = []
    for pid in sorted(groups):
        items = sorted(groups[pid])
        max_fy = max(fy for _, fy in items)
        auth = [key for key, fy in items if fy == max_fy]
        for key, fy in items:
            if fy != max_fy:
                disposition[key] = ("superseded", "R1", f"evaluation_fy={fy} below project maximum {max_fy}")
        stage = {key: _record_exclusion(key, max_fy, approval, crosswalk) for key in auth}
        if len(auth) > 1:
            used = {key: _used_values(approval[key], crosswalk[key]) for key in auth}
            if len(set(used.values())) > 1:
                detail = "; ".join(sorted(f"{locator_text(k)}={v[0]}/{v[1]}" for k, v in used.items()))
                for key in auth:
                    if stage[key] is None:
                        stage[key] = ("D1", detail)
        survivors = []
        for key in auth:
            if stage[key] is None:
                disposition[key] = ("candidate_record", "", "")
                survivors.append(key)
            else:
                disposition[key] = ("excluded", stage[key][0], stage[key][1])
        if not survivors:
            primary = min((stage[k][0] for k in auth), key=lambda code: RULE_ORDER[code])
            project_status[pid] = ("excluded", primary)
            continue
        project_status[pid] = ("candidate", "")
        candidates.append(_candidate_row(pid, max_fy, survivors, items, approval, crosswalk, oghist))
    return disposition, project_status, candidates, len(groups)


def _candidate_row(pid, max_fy, survivors, items, approval, crosswalk, oghist):
    first = survivors[0]
    fy_used = approval[first].resolved
    wb_used = crosswalk[first].wb_code.strip().upper()
    for key in survivors:
        if approval[key].resolved != fy_used or crosswalk[key].wb_code.strip().upper() != wb_used:
            raise IntegrityError(f"{pid}: surviving tied records disagree on used values")
    cell = (wb_used, fy_used)
    if cell not in oghist:
        presence = "absent"
    elif oghist[cell] == "":
        presence = "known"
    else:
        presence = "unknown_kind"
    window = "development" if max_fy <= DEVELOPMENT_MAX_EVAL_FY else "holdout"
    if window == "holdout" and not (HOLDOUT_MIN_EVAL_FY <= max_fy <= HOLDOUT_MAX_EVAL_FY):
        raise IntegrityError(f"{pid}: evaluation_fy {max_fy} is outside both windows")
    return {
        "project_id": pid,
        "window": window,
        "evaluation_fy": max_fy,
        "approval_fy": fy_used,
        "approval_status": "|".join(sorted({approval[k].status for k in survivors})),
        "approval_rule": "|".join(sorted({approval[k].rule_id for k in survivors})),
        "wb_code": wb_used,
        "crosswalk_status": "|".join(sorted({crosswalk[k].status for k in survivors})),
        "crosswalk_rule": "|".join(sorted({crosswalk[k].rule_id for k in survivors})),
        "oghist_cell_presence": presence,
        "record_count": len(items),
        "authoritative_records": ";".join(locator_text(k) for k in sorted(survivors)),
        "source_records": ";".join(locator_text(k) for k, _ in items),
        "pno_record_found": "|".join(sorted({crosswalk[k].pno_record_found for k in survivors})),
    }


CANDIDATE_FIELDS = [
    "project_id", "window", "evaluation_fy", "approval_fy", "approval_status", "approval_rule",
    "wb_code", "crosswalk_status", "crosswalk_rule", "oghist_cell_presence", "record_count",
    "authoritative_records", "source_records", "pno_record_found",
]
EXCLUSION_FIELDS = [
    "project_id", "ieg_page_file", "ieg_index", "disposition", "rule_id", "primary_reason", "detail",
]


def _csv_bytes(fields, rows) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({f: row.get(f, "") for f in fields})
    return buf.getvalue().encode("utf-8")


def _json_bytes(obj) -> bytes:
    return (json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _check_lineage(records, approval, crosswalk):
    """Every record must have one approval row and one crosswalk row, with the same project_id."""
    keys = {(r.page, r.index) for r in records}
    if set(approval) != keys:
        raise IntegrityError("approval rows do not cover the snapshot records exactly")
    if set(crosswalk) != keys:
        raise IntegrityError("crosswalk rows do not cover the snapshot records exactly")
    for rec in records:
        key = (rec.page, rec.index)
        if approval[key].project_id != rec.project_id or crosswalk[key].project_id != rec.project_id:
            raise IntegrityError(f"project_id disagrees across inputs at {locator_text(key)}")
        if approval[key].status in APPROVAL_ACCEPTED and approval[key].resolved != rec.approval_raw:
            raise IntegrityError(f"step C resolved FY differs from the IEG approval_fy at {locator_text(key)}")


def build_outputs(records, pins_info, approval, crosswalk, oghist, tool_info, approval_sha, crosswalk_sha, oghist_sha):
    disposition, project_status, candidates, valid_projects = decide(records, approval, crosswalk, oghist)
    candidates.sort(key=lambda r: r["project_id"])

    by_key = {(r.page, r.index): r for r in records}
    exclusion_rows = []
    for key in sorted(disposition, key=lambda k: (str(by_key[k].project_id), k[0], k[1])):
        kind, code, detail = disposition[key]
        if kind == "candidate_record":
            continue
        rec = by_key[key]
        exclusion_rows.append({
            "project_id": rec.project_id if isinstance(rec.project_id, str) else "",
            "ieg_page_file": key[0],
            "ieg_index": key[1],
            "disposition": kind,
            "rule_id": code,
            "primary_reason": REASON_BY_RULE.get(code, ""),
            "detail": detail,
        })

    counts = defaultdict(int)
    for kind, code, _ in disposition.values():
        counts[kind] += 1
    excluded_by_rule = {code: 0 for code, _, _ in RULES if code != "R1"}
    for kind, code, _ in disposition.values():
        if kind == "excluded":
            excluded_by_rule[code] += 1
    records_in = len(records)
    accounted = counts["candidate_record"] + counts["superseded"] + counts["excluded"]
    if accounted != records_in or set(disposition) != {(r.page, r.index) for r in records}:
        raise IntegrityError(f"record accounting failed: {accounted} accounted of {records_in}")

    windows = defaultdict(list)
    for row in candidates:
        windows[row["window"]].append(row)
    dev = windows["development"]
    hold = windows["holdout"]
    per_country = {}
    for name, rows in (("development", dev), ("holdout", hold)):
        tally = defaultdict(int)
        for row in rows:
            tally[row["wb_code"]] += 1
        per_country[name] = tally
    countries_below = {name: sum(1 for n in tally.values() if n < SMALL_COUNTRY_N) for name, tally in per_country.items()}
    presence = defaultdict(int)
    pno_missing = 0
    fy_before_approval = 0
    approval_status_counts = defaultdict(int)
    for row in candidates:
        presence[row["oghist_cell_presence"]] += 1
        if row["pno_record_found"] == "false":
            pno_missing += 1
        if row["evaluation_fy"] < row["approval_fy"]:
            fy_before_approval += 1
        approval_status_counts[row["approval_status"]] += 1

    excluded_projects = defaultdict(int)
    for pid, (state, primary) in project_status.items():
        if state == "excluded":
            excluded_projects[primary] += 1
    projects_candidate = sum(1 for s, _ in project_status.values() if s == "candidate")
    projects_excluded = valid_projects - projects_candidate
    if projects_excluded != sum(excluded_projects.values()):
        raise IntegrityError("project accounting failed")

    dev_ids = sorted(r["project_id"] for r in dev)
    hold_ids = sorted(r["project_id"] for r in hold)
    if set(dev_ids) & set(hold_ids):
        raise IntegrityError("a project appears in both windows")

    qa = {
        "statement": STATEMENT,
        "structural_candidate_N": len(candidates),
        "not_rated_eligible_N": "not determined by this step; rating-based eligibility needs the separate R1 target assembly",
        "records_in": records_in,
        "records_by_disposition": {
            "candidate_record": counts["candidate_record"],
            "superseded": counts["superseded"],
            "excluded": counts["excluded"],
        },
        "excluded_records_by_rule_in_order": {code: excluded_by_rule[code] for code, _, _ in RULES if code != "R1"},
        "projects": {
            "valid_project_ids": valid_projects,
            "candidates": projects_candidate,
            "excluded": projects_excluded,
            "excluded_by_primary_rule_in_order": {code: excluded_projects.get(code, 0) for code, _, _ in RULES if code != "R1"},
        },
        "candidates_by_window": {"development": len(dev), "holdout": len(hold), "total": len(candidates)},
        "distinct_countries_by_window": {
            "development": len(per_country["development"]),
            "holdout": len(per_country["holdout"]),
            "overall": len({r["wb_code"] for r in candidates}),
        },
        "countries_below_5_projects_by_window": countries_below,
        "oghist_cell_presence_among_candidates": {k: presence.get(k, 0) for k in ("known", "unknown_kind", "absent")},
        "candidates_with_no_pno_record": pno_missing,
        "candidates_by_approval_status": dict(sorted(approval_status_counts.items())),
        "candidates_with_evaluation_fy_before_approval_fy": fy_before_approval,
        "conservation": {
            "records_in": records_in,
            "accounted": accounted,
            "ok": accounted == records_in,
        },
        "snapshot_as_of": SNAPSHOT_AS_OF_RAW,
        "rule_version": RULE_VERSION,
    }

    policy = {
        "policy": "ieg-structural-eligibility",
        "rule_version": RULE_VERSION,
        "statement": STATEMENT,
        "inputs": pins_info["pins"],
        "labels": {
            "read": [],
            "whitelist_fields_from_ieg_pages": list(WHITELIST),
            "row_locator": ["ieg_page_file", "ieg_index"],
            "rating_or_outcome_fields_read": False,
            "rated_or_not_rated_filter_applied": False,
        },
        "thresholds": {
            "approval_fy_min_inclusive": APPROVAL_FY_MIN,
            "development_evaluation_fy_max_inclusive": DEVELOPMENT_MAX_EVAL_FY,
            "holdout_evaluation_fy_range_inclusive": [HOLDOUT_MIN_EVAL_FY, HOLDOUT_MAX_EVAL_FY],
            "evaluation_fy_excluded_from_inclusive": EVAL_FY_EXCLUDED_FROM,
            "small_country_n_below": SMALL_COUNTRY_N,
            "snapshot_as_of_date": SNAPSHOT_AS_OF_RAW,
            "evaluation_fy_pattern": "4 ASCII digits or JSON integer in 1000-9999",
            "project_id_pattern": "P followed by six ASCII digits",
        },
        "approval_accepted_statuses": list(APPROVAL_ACCEPTED),
        "approval_quarantine_status": APPROVAL_QUARANTINE,
        "country_accepted_statuses": list(COUNTRY_ACCEPTED),
        "country_excluded_statuses": COUNTRY_EXCLUDED_RULE,
        "rule_order": [
            {"order": i + 1, "rule_id": code, "reason": reason, "text": text}
            for i, (code, reason, text) in enumerate(RULES)
        ],
        "stage_order_note": (
            "Per record: V1, V2, V3, E0 (validity). Per project: the maximum evaluation FY "
            "defines the authoritative records (R1 marks the others superseded). Per authoritative "
            "record, in order: A1, A2, E2, then C1, C2, C3. Then D1 applies to all tied "
            "authoritative records when their used values (approval FY, resolved country) differ "
            "and none of them is already excluded by an earlier rule for that record. A project with no "
            "surviving authoritative record is excluded; its primary reason is the earliest rule in rule_order."
        ),
        "window_rule": "evaluation FY 1000-2019 -> development; 2020-2026 -> holdout; 2027 or later excluded (E2)",
        "dedupe_rule": "maximum evaluation FY; equal-FY records are used only if their canonical used values (approval FY as integer, wb_code upper-cased) are identical; all source locators are kept",
        "tie_breaker": "none; API order is never used",
        "oghist_rule": "presence only for (wb_code, approval FY): known | unknown_kind | absent; never an exclusion; the income group is never read",
        "missing_pno_rule": "a missing P&O record is never an exclusion (reported as candidates_with_no_pno_record)",
    }

    candidate_bytes = _csv_bytes(CANDIDATE_FIELDS, candidates)
    exclusion_bytes = _csv_bytes(EXCLUSION_FIELDS, exclusion_rows)
    dev_bytes = ("".join(f"{pid}\n" for pid in dev_ids)).encode("utf-8")
    hold_bytes = ("".join(f"{pid}\n" for pid in hold_ids)).encode("utf-8")
    policy_bytes = _json_bytes(policy)
    qa_bytes = _json_bytes(qa)
    files = {
        "structural_candidates.csv": candidate_bytes,
        "structural_exclusions.csv": exclusion_bytes,
        "split_ids_development.txt": dev_bytes,
        "split_ids_holdout.txt": hold_bytes,
        "split_policy.json": policy_bytes,
        "structural_qa.json": qa_bytes,
    }
    manifest = {
        "rule_version": RULE_VERSION,
        "statement": STATEMENT,
        "tool": tool_info,
        "inputs": {
            "snapshot": {
                "manifest_sha256": pins_info["pins"]["snapshot_manifest_sha256"],
                "page_digest": pins_info["pins"]["snapshot_page_digest"],
                "as_of_date": SNAPSHOT_AS_OF_RAW,
                "ieg_page_sha256": pins_info["ieg_page_sha256"],
            },
            "approval": {"manifest_sha256": pins_info["pins"]["approval_manifest_sha256"], "rows_file": APPROVAL_ROWS, "rows_sha256": approval_sha},
            "crosswalk": {"manifest_sha256": pins_info["pins"]["crosswalk_manifest_sha256"], "rows_file": CROSSWALK_ROWS, "rows_sha256": crosswalk_sha},
            "oghist": {"manifest_sha256": pins_info["pins"]["oghist_manifest_sha256"], "rows_file": OGHIST_ROWS, "rows_sha256": oghist_sha},
        },
        "outputs": {name: {"bytes": len(data), "sha256": sha256_bytes(data)} for name, data in files.items()},
    }
    return files, manifest, qa


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def default_tool_info() -> dict:
    repo = Path(__file__).resolve().parents[1]
    return {
        "path": "scripts/ieg_structural_eligibility.py",
        "git_sha": _git(repo, "rev-parse", "HEAD").strip(),
        "git_dirty": bool(_git(repo, "status", "--porcelain").strip()),
        "script_sha256": sha256_bytes(Path(__file__).resolve().read_bytes()),
        "python": sys.version.split()[0],
    }


def run(pins: Pins, out_dir: Path, tool_info: dict | None = None) -> dict:
    out_dir = Path(out_dir)
    if out_dir.exists() or out_dir.with_name(out_dir.name + ".partial").exists():
        raise FileExistsError(f"refusing to overwrite {out_dir}")
    tool = tool_info if tool_info is not None else default_tool_info()
    records, snap_info = load_snapshot(pins)
    approval, approval_sha = load_approval(pins)
    crosswalk, crosswalk_sha, crosswalk_oghist_sha = load_crosswalk(pins, approval_sha)
    oghist, oghist_sha = load_oghist(pins)
    if crosswalk_oghist_sha != oghist_sha:
        raise PinRefused("crosswalk: built from a different income_history.csv")
    _check_lineage(records, approval, crosswalk)
    pins_info = {
        "pins": {
            "snapshot_manifest_sha256": pins.snapshot_manifest_sha,
            "snapshot_page_digest": pins.snapshot_page_digest,
            "approval_manifest_sha256": pins.approval_manifest_sha,
            "crosswalk_manifest_sha256": pins.crosswalk_manifest_sha,
            "oghist_manifest_sha256": pins.oghist_manifest_sha,
        },
        "ieg_page_sha256": snap_info["ieg_page_sha256"],
    }
    files, manifest, qa = build_outputs(records, pins_info, approval, crosswalk, oghist, tool, approval_sha, crosswalk_sha, oghist_sha)
    manifest_bytes = _json_bytes(manifest)
    files["manifest.json"] = manifest_bytes
    _write_atomically(out_dir, files)
    return {"out_dir": str(out_dir), "manifest_sha256": sha256_bytes(manifest_bytes), "qa": qa, "files": files}


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_atomically(out_dir: Path, files: dict[str, bytes]) -> None:
    partial = out_dir.with_name(out_dir.name + ".partial")
    os.mkdir(partial)
    for name, data in files.items():
        with open(partial / name, "xb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    _fsync_dir(partial)
    if out_dir.exists():
        raise FileExistsError(f"{out_dir} appeared during the run")
    os.rename(partial, out_dir)
    _fsync_dir(out_dir.parent)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, help="new output directory; must not exist")
    args = parser.parse_args(argv)
    result = run(REAL_PINS, Path(args.out))
    qa = result["qa"]
    print(f"output: {result['out_dir']}")
    print(f"manifest.json sha256: {result['manifest_sha256']}")
    for name in sorted(result["files"]):
        print(f"{sha256_bytes(result['files'][name])}  {name}")
    print(STATEMENT + f": N={qa['structural_candidate_N']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
