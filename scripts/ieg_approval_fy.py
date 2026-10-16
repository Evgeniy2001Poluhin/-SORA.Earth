#!/usr/bin/env python3
"""Approval date <-> World Bank fiscal year QA for the IEG pilot, with no label values.

Reads the pinned World Bank snapshot read-only and writes a new output
directory: one row per IEG record with its approval fiscal year resolved from
the P&O approval date and the IEG approval_fy, the conflicts that need a
disposition, the counts, the rules, and a manifest.

Standard library only. The script opens no network connection and imports
nothing from the application. Every record is read through an explicit
projection: IEG_FIELDS for IEG records, PNO_FIELDS for P&O records. Asking a
record for any other key raises ProjectionError.

Dates are read as the UTC calendar date. The host timezone is never consulted.

Usage:
    python scripts/ieg_approval_fy.py --snapshot DIR --out DIR

Exit status: 0 published; 1 refused or failed before the rename, so no final
directory exists; 3 published, but the fsync of the parent directory failed,
so durability is not confirmed.
"""

import argparse
import csv
import hashlib
import io
import json
import os
import platform
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

SCHEMA_VERSION = "1"
TOOL_PATH = "scripts/ieg_approval_fy.py"

PINNED_MANIFEST_SHA256 = "b3d43f8033367d79bb14ace692f8cada7068e98a3540fdfbbc0a1bea2505474c"
PINNED_PAGE_DIGEST = "960d776e8db25a59abcb3562e921c1643ace32466d71682c017b2912dc43d4f3"

# The only keys this tool asks of a record. Anything else in a record is never read.
IEG_FIELDS = ("project_id", "approval_fy", "evaluation_fy", "as_of_date")
PNO_FIELDS = ("id", "boardapprovaldate", "approvalfy")

MIN_DATE = date(1944, 1, 1)
MIN_FY = 1944

OUT_ROWS = "project_approval_no_y.csv"
OUT_CONFLICTS = "approval_conflicts.csv"
OUT_QA = "approval_qa.json"
OUT_RULES = "fy_rules.md"
OUT_MANIFEST = "manifest.json"
DATA_OUTPUTS = (OUT_ROWS, OUT_CONFLICTS, OUT_QA, OUT_RULES)

ACCEPTED = "accepted"
ACCEPTED_WITH_AUX = "accepted_with_aux_warning"
FY_ONLY = "fy_only"
QUARANTINE = "quarantine"
STATUSES = (ACCEPTED, ACCEPTED_WITH_AUX, FY_ONLY, QUARANTINE)

R_BOARD_INVALID = "board_date_invalid"
R_BOARD_TZ = "board_date_timezone_ambiguous"
R_BOARD_RANGE = "board_date_out_of_range"
R_BOARD_FUTURE = "board_date_after_snapshot"
R_DATE_FY = "date_ieg_fy_conflict"
R_IEG_ABSENT = "ieg_fy_absent_with_board_date"
R_IEG_INVALID = "ieg_fy_invalid_with_board_date"
R_NEITHER = "neither_usable_source"
R_DUP = "ieg_duplicate_fy_disagreement"
R_FY_ONLY = "board_date_absent"
A_DISAGREE = "aux_pno_approvalfy_disagrees"
A_INVALID = "aux_pno_approvalfy_invalid"
C_DUP = "ieg_duplicate_project_id"

KIND_APPROVAL = "approval_date"
KIND_FY_START = "fy_start_NOT_an_approval_date"

ABSENT_TEXT = "<missing>"

RULE_FOR_QUARANTINE = {
    R_DATE_FY: "FY-04",
    R_NEITHER: "FY-05",
    R_BOARD_INVALID: "FY-06",
    R_BOARD_TZ: "FY-07",
    R_BOARD_RANGE: "FY-08",
    R_BOARD_FUTURE: "FY-08",
    R_IEG_INVALID: "FY-09",
    R_IEG_ABSENT: "FY-10",
    R_DUP: "FY-11",
}

RULES = (
    ("FY-01", ACCEPTED, "C.3. Valid approval date; IEG approval_fy equals the computed FY; P&O approvalfy absent or equal to it. Resolved FY = computed FY, availability = the approval date."),
    ("FY-02", ACCEPTED_WITH_AUX, "C.3 and C.7. As FY-01, but P&O approvalfy is present and differs from the computed FY, or is not a valid FY. Recorded as an untrusted auxiliary warning; the resolved FY is unchanged."),
    ("FY-03", FY_ONLY, "C.5. No approval date (no P&O record, or no boardapprovaldate) and a valid IEG approval_fy. Resolved FY = IEG approval_fy. Availability = 1 July of the FY before it, marked as NOT an approval date. P&O approvalfy is not used."),
    ("FY-04", QUARANTINE, "C.4. Valid approval date and valid IEG approval_fy that differ. Not corrected: the approval evidence must be checked and an explicit override recorded before registration."),
    ("FY-05", QUARANTINE, "C.6. No approval date and no valid IEG approval_fy (absent or invalid)."),
    ("FY-06", QUARANTINE, "C.1 and C.6. The approval date is present but is not a valid date: wrong type, wrong format, or an impossible calendar date (for example 29 February in a non-leap year). Never treated as absent."),
    ("FY-07", QUARANTINE, "C.1. The approval date carries a timezone other than UTC (Z or +00:00), or a time with no zone. Not converted: the calendar date it names is ambiguous."),
    ("FY-08", QUARANTINE, "C.2. The approval date is before 1944-01-01, or after the snapshot as-of date."),
    ("FY-09", QUARANTINE, "C.2. A valid approval date, and an IEG approval_fy that is present but invalid (boolean, non-integer, or out of range)."),
    ("FY-10", QUARANTINE, "Interpretation, flagged for review. A valid approval date with no IEG approval_fy. C.3 accepts only on agreement with the IEG FY, and there is none to check against."),
    ("FY-11", QUARANTINE, "Interpretation of B.7 applied to approval. A repeated IEG project_id whose records disagree on approval_fy. Neither record is preferred; both are quarantined."),
)

REVIEW_NOTE = {
    R_DATE_FY: "The approval date and the IEG FY disagree. Check the original approval evidence. No override is applied here.",
    R_IEG_ABSENT: "The approval date is valid but the IEG approval_fy is missing. A decision is needed before use.",
    R_IEG_INVALID: "The IEG approval_fy is present but invalid. Check the source value.",
    R_NEITHER: "Neither a usable approval date nor a valid IEG approval_fy exists. Excluded until reviewed.",
    R_BOARD_INVALID: "The approval date is present but not valid. Check the source value.",
    R_BOARD_TZ: "The approval date names no UTC instant. Check the convention used by the source.",
    R_BOARD_RANGE: "The approval date is before 1944-01-01. Check the source value.",
    R_BOARD_FUTURE: "The approval date is after the snapshot as-of date. Check the source value.",
    R_DUP: "A repeated IEG project_id whose records disagree on approval_fy. Neither record is preferred.",
    C_DUP: "A repeated IEG project_id. Both records are kept, identified by their locator.",
    A_DISAGREE: "The P&O approvalfy differs from the computed FY. Auxiliary only; the resolved FY is unchanged.",
    A_INVALID: "The P&O approvalfy is present but not a valid FY. Auxiliary only; the resolved FY is unchanged.",
}

# The row locator is (ieg_page_file, ieg_index). The page's sha256 is not written into
# derived rows: it changes whenever any value in the page changes, label values included,
# which would make the derived bytes depend on y. The per-page sha256 values are pinned
# in manifest.json under inputs.pages instead.
ROW_COLUMNS = (
    "project_id",
    "ieg_page_file",
    "ieg_index",
    "ieg_as_of_date_raw",
    "ieg_approval_fy_raw",
    "ieg_evaluation_fy_raw",
    "pno_record_found",
    "pno_key",
    "pno_id_raw",
    "pno_boardapprovaldate_raw",
    "pno_approvalfy_raw",
    "board_date_state",
    "board_date_parsed",
    "board_date_reason",
    "computed_wb_fy",
    "ieg_fy_state",
    "ieg_fy_value",
    "ieg_fy_reason",
    "aux_state",
    "resolved_wb_fy",
    "status",
    "reason",
    "rule_id",
    "availability_bound",
    "availability_bound_kind",
    "ieg_duplicate_count",
    "detail",
)

CONFLICT_COLUMNS = (
    "project_id",
    "ieg_page_file",
    "ieg_index",
    "status",
    "reason",
    "rule_id",
    "conflict_categories",
    "ieg_approval_fy_raw",
    "pno_boardapprovaldate_raw",
    "pno_approvalfy_raw",
    "computed_wb_fy",
    "ieg_fy_value",
    "aux_state",
    "detail",
    "review_note",
    "disposition",
)

_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}
_AS_OF = re.compile(r"([0-9]{2})-([A-Za-z]{3})-([0-9]{4})")
_DATE_ONLY = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
_DATE_UTC = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.[0-9]{1,9})?(?:Z|\+00:00)"
)
_TIME_PREFIX = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T")
_FY_STRING = re.compile(r"[0-9]{4}")
_PAGE_PATH = re.compile(r"(ieg|projects)/page-[0-9]{5}\.json")
_PAGE_NAME = re.compile(r"page-[0-9]{5}\.json")


class ToolError(Exception):
    """A refusal reported as a message, without a traceback."""


class PinMismatch(ToolError):
    """The snapshot does not match the pinned manifest or page digest."""


class SourceError(ToolError):
    """The snapshot is malformed, or its records cannot be reconciled."""


class ProjectionError(ToolError):
    """Code asked a record for a field outside the explicit projection."""


class OutputRefused(ToolError):
    """The destination cannot be used. Nothing was written."""


class OutputFailed(ToolError):
    """Writing failed before the rename. No final directory exists."""


class DurabilityUnconfirmed(ToolError):
    """The directory was renamed into place, but the parent fsync failed."""


@dataclass(frozen=True)
class Pins:
    manifest_sha256: str
    page_digest: str


DEFAULT_PINS = Pins(PINNED_MANIFEST_SHA256, PINNED_PAGE_DIGEST)


class _Absent:
    def __repr__(self):
        return "ABSENT"


_ABSENT = _Absent()


class _Projection:
    """Read access to the allowed keys of one raw record, and to no others."""

    __slots__ = ("_raw", "_allowed")

    def __init__(self, raw, allowed):
        self._raw = raw
        self._allowed = allowed

    def get(self, key):
        if key not in self._allowed:
            raise ProjectionError(f"field {key!r} is outside the projection")
        return self._raw.get(key, _ABSENT)


def project(raw, allowed):
    """Return exactly the allowed fields of a raw record. Missing keys map to _ABSENT."""
    view = _Projection(raw, allowed)
    return {key: view.get(key) for key in allowed}


def _strict_json(body, where):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise SourceError(f"{where}: duplicate key in a JSON object")
            out[key] = value
        return out

    def refuse_constant(name):
        raise SourceError(f"{where}: non-finite number is not allowed")

    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise SourceError(f"{where}: not UTF-8") from None
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=refuse_constant)
    except json.JSONDecodeError as exc:
        raise SourceError(f"{where}: not valid JSON (line {exc.lineno})") from None


def _calendar_date(year, month, day):
    """The calendar date as written. date() rejects impossible days, leap years included."""
    return date(year, month, day)


def fiscal_year(d):
    """WB fiscal year K covers [01 Jul (K-1), 01 Jul K), so a July or later date falls in the next FY."""
    return d.year + (1 if d.month >= 7 else 0)


def parse_as_of(text):
    m = _AS_OF.fullmatch(text)
    if m is None or m.group(2) not in _MONTHS:
        raise SourceError("the snapshot as_of_date is not dd-Mon-yyyy with an English month")
    try:
        return date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))
    except ValueError:
        raise SourceError("the snapshot as_of_date is not a calendar date") from None


def parse_board_date(raw, as_of):
    """Return (state, date or None, reason or None); state is absent, valid or invalid.

    A date-only value is its calendar date. A value with a time is read only with
    a UTC zone (Z or +00:00), and its UTC calendar date is used. Any other zone,
    or no zone, is quarantined. Nothing consults the host timezone.
    """
    if raw is _ABSENT or raw is None:
        return "absent", None, None
    if not isinstance(raw, str):
        return "invalid", None, R_BOARD_INVALID
    m = _DATE_ONLY.fullmatch(raw)
    if m is None:
        m = _DATE_UTC.fullmatch(raw)
        if m is None:
            if _TIME_PREFIX.match(raw):
                return "invalid", None, R_BOARD_TZ
            return "invalid", None, R_BOARD_INVALID
        hour, minute, second = int(m.group(4)), int(m.group(5)), int(m.group(6))
        if hour > 23 or minute > 59 or second > 59:
            return "invalid", None, R_BOARD_INVALID
    try:
        d = _calendar_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return "invalid", None, R_BOARD_INVALID
    if d < MIN_DATE:
        return "invalid", None, R_BOARD_RANGE
    if d > as_of:
        return "invalid", None, R_BOARD_FUTURE
    return "valid", d, None


def parse_fy(raw, max_fy):
    """Return (state, int or None, detail or None). A bool is never a FY.

    Accepted: a JSON integer, or a string of exactly four ASCII digits. Both must
    lie in [MIN_FY, max_fy]. Whitespace, signs, decimals and Unicode digits are refused.
    """
    if raw is _ABSENT or raw is None:
        return "absent", None, None
    if isinstance(raw, bool):
        return "invalid", None, "fy_bool"
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and _FY_STRING.fullmatch(raw):
        value = int(raw)
    else:
        return "invalid", None, "fy_not_integer"
    if not MIN_FY <= value <= max_fy:
        return "invalid", None, "fy_out_of_range"
    return "valid", value, None


def _raw_text(value):
    if value is _ABSENT:
        return ABSENT_TEXT
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _git_state(cwd):
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, timeout=30, check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True, timeout=30, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None, None
    return sha, bool(status.strip())


def _check_destination(out):
    """Refuse before anything is read. The parent must exist; it is not created here."""
    parent = out.parent
    partial = out.with_name(out.name + ".partial")
    if not parent.is_dir():
        raise OutputRefused(f"parent directory {parent} does not exist; this tool does not create it")
    if os.path.lexists(out):
        if out.is_symlink() or not out.is_dir() or any(out.iterdir()):
            raise OutputRefused(f"{out} exists and is not an empty directory")
    if os.path.lexists(partial):
        raise OutputRefused(f"{partial} exists; an earlier run did not finish, and it is not removed automatically")


def _load_snapshot(snapshot, pins):
    """Verify the pins and every page before any record is read. Return the pages in order."""
    man_bytes = (snapshot / "manifest.json").read_bytes()
    if hashlib.sha256(man_bytes).hexdigest() != pins.manifest_sha256:
        raise PinMismatch("manifest.json does not match the pinned sha256")
    manifest = _strict_json(man_bytes, "manifest.json")
    try:
        datasets = manifest["datasets"]
        listing = {name: datasets[name]["pages"] for name in ("ieg", "projects")}
        as_of_texts = datasets["ieg"]["as_of_dates"]
    except (KeyError, TypeError):
        raise SourceError("manifest.json lacks the dataset structure this tool reads") from None
    if not (isinstance(as_of_texts, list) and len(as_of_texts) == 1 and isinstance(as_of_texts[0], str)):
        raise SourceError("the IEG snapshot must carry exactly one as_of_date; mixed vintages are refused")

    pages = {}
    digest_lines = []
    for name in ("ieg", "projects"):
        entries = listing[name]
        if not isinstance(entries, list) or not entries:
            raise SourceError(f"{name}: no pages listed in the manifest")
        loaded = []
        for entry in entries:
            try:
                rel = entry["file"]
                want_sha = entry["sha256"]
                want_bytes = entry["bytes"]
            except (KeyError, TypeError):
                raise SourceError(f"{name}: a page entry lacks file, sha256 or bytes") from None
            if not isinstance(rel, str) or not _PAGE_PATH.fullmatch(rel) or rel.split("/")[0] != name:
                raise SourceError(f"{name}: a manifest page path is not of the expected form")
            body = (snapshot / rel).read_bytes()
            got = hashlib.sha256(body).hexdigest()
            if got != want_sha or len(body) != want_bytes:
                raise SourceError(f"{rel}: bytes or sha256 differ from the manifest")
            loaded.append((rel, got, body))
            digest_lines.append(f"{got}  {rel}")
        on_disk = {p for p in os.listdir(snapshot / name) if not p.startswith(".")}
        if on_disk != {rel.split("/")[1] for rel, _, _ in loaded}:
            raise SourceError(f"{name}/: files on disk differ from the manifest's page list")
        if not all(_PAGE_NAME.fullmatch(p) for p in on_disk):
            raise SourceError(f"{name}/: an unexpected file is present")
        loaded.sort(key=lambda item: item[0])
        pages[name] = loaded

    digest = hashlib.sha256(
        "".join(line + "\n" for line in sorted(digest_lines)).encode("utf-8")
    ).hexdigest()
    if digest != pins.page_digest:
        raise PinMismatch("the page digest does not match the pin")
    return manifest, pages, as_of_texts[0]


@dataclass
class IegRecord:
    page_file: str
    index: int
    project_id: str
    fields: dict


def _parse_ieg(pages, as_of_text):
    records = []
    for rel, _sha, body in pages:
        doc = _strict_json(body, rel)
        data = doc.get("data") if isinstance(doc, dict) else None
        if not isinstance(data, list):
            raise SourceError(f"{rel}: no 'data' list")
        for index, raw in enumerate(data):
            if not isinstance(raw, dict):
                raise SourceError(f"{rel}[{index}]: record is not an object")
            fields = project(raw, IEG_FIELDS)
            pid = fields["project_id"]
            if not isinstance(pid, str) or pid == "":
                raise SourceError(f"{rel}[{index}]: no usable project_id")
            if fields["as_of_date"] != as_of_text:
                raise SourceError(f"{rel}[{index}]: as_of_date differs from the snapshot's")
            records.append(IegRecord(page_file=rel, index=index, project_id=pid, fields=fields))
    return records


def _parse_pno(pages):
    out = {}
    for rel, _sha, body in pages:
        doc = _strict_json(body, rel)
        projects = doc.get("projects") if isinstance(doc, dict) else None
        if not isinstance(projects, dict):
            raise SourceError(f"{rel}: no 'projects' mapping")
        for key, raw in projects.items():
            if not isinstance(raw, dict):
                raise SourceError(f"{rel}: a record under a key is not an object")
            fields = project(raw, PNO_FIELDS)
            if fields["id"] != key:
                raise SourceError(f"{rel}: a record's id differs from its key")
            if key in out:
                raise SourceError(f"{rel}: the same key appears more than once")
            out[key] = fields
    return out


@dataclass
class Row:
    project_id: str
    page_file: str
    index: int
    as_of_raw: object
    ieg_fy_raw: object
    ieg_eval_raw: object
    pno_found: bool
    pno_key: str
    pno_id_raw: object
    pno_date_raw: object
    pno_fy_raw: object
    board_state: str
    board_date: object
    board_reason: object
    computed_fy: object
    ieg_state: str
    ieg_fy: object
    ieg_reason: object
    aux_state: str
    resolved_fy: object = None
    status: str = ""
    reason: str = ""
    availability: str = ""
    availability_kind: str = ""
    dup_count: int = 1
    dup_disagree: bool = False
    categories: set = field(default_factory=set)
    detail: str = ""


def _get(fields, key):
    return _ABSENT if fields is None else fields[key]


def _quarantine(r, reason):
    r.status, r.reason = QUARANTINE, reason
    r.resolved_fy = None
    r.availability = ""
    r.availability_kind = ""


def _decide(r):
    """Apply C.3 to C.7. The approval date is the basis; IEG approval_fy is the cross-check."""
    if r.board_state == "invalid":
        _quarantine(r, r.board_reason)
        return
    if r.board_state == "valid":
        if r.ieg_state == "absent":
            _quarantine(r, R_IEG_ABSENT)
            return
        if r.ieg_state == "invalid":
            _quarantine(r, R_IEG_INVALID)
            return
        if r.ieg_fy != r.computed_fy:
            _quarantine(r, R_DATE_FY)
            return
        r.resolved_fy = r.computed_fy
        r.availability = r.board_date.isoformat()
        r.availability_kind = KIND_APPROVAL
        if r.aux_state in ("absent", "agrees"):
            r.status, r.reason = ACCEPTED, ""
        else:
            r.status = ACCEPTED_WITH_AUX
            r.reason = A_INVALID if r.aux_state == "invalid" else A_DISAGREE
        return
    if r.ieg_state == "valid":
        r.status, r.reason = FY_ONLY, R_FY_ONLY
        r.resolved_fy = r.ieg_fy
        r.availability = date(r.ieg_fy - 1, 7, 1).isoformat()
        r.availability_kind = KIND_FY_START
        return
    _quarantine(r, R_NEITHER)


def _detail(r):
    board = f"board={r.board_state}" + (f":{r.board_reason}" if r.board_reason else "")
    ieg = f"ieg={r.ieg_state}" + (f":{r.ieg_reason}" if r.ieg_reason else "")
    return "; ".join([board, ieg, f"aux={r.aux_state}"])


def _build_row(rec, pno_fields, as_of):
    max_fy = fiscal_year(as_of)
    board_state, board_date, board_reason = parse_board_date(_get(pno_fields, "boardapprovaldate"), as_of)
    ieg_state, ieg_fy, ieg_reason = parse_fy(rec.fields["approval_fy"], max_fy)
    aux_parse, aux_fy, _ = parse_fy(_get(pno_fields, "approvalfy"), max_fy)
    computed = fiscal_year(board_date) if board_state == "valid" else None
    if aux_parse == "absent":
        aux_state = "absent"
    elif aux_parse == "invalid":
        aux_state = "invalid"
    elif board_state == "valid":
        aux_state = "agrees" if aux_fy == computed else "disagrees"
    else:
        aux_state = "unused"
    row = Row(
        project_id=rec.project_id,
        page_file=rec.page_file,
        index=rec.index,
        as_of_raw=rec.fields["as_of_date"],
        ieg_fy_raw=rec.fields["approval_fy"],
        ieg_eval_raw=rec.fields["evaluation_fy"],
        pno_found=pno_fields is not None,
        pno_key=rec.project_id if pno_fields is not None else "",
        pno_id_raw=_get(pno_fields, "id"),
        pno_date_raw=_get(pno_fields, "boardapprovaldate"),
        pno_fy_raw=_get(pno_fields, "approvalfy"),
        board_state=board_state,
        board_date=board_date,
        board_reason=board_reason,
        computed_fy=computed,
        ieg_state=ieg_state,
        ieg_fy=ieg_fy,
        ieg_reason=ieg_reason,
        aux_state=aux_state,
    )
    _decide(row)
    row.detail = _detail(row)
    return row


def _apply_duplicates(rows):
    """A repeated IEG project_id keeps every record by locator. Disagreement on approval_fy is
    not resolved by choosing a side: each record of the group is quarantined."""
    groups = defaultdict(list)
    for r in rows:
        groups[r.project_id].append(r)
    for members in groups.values():
        if len(members) == 1:
            continue
        texts = {_raw_text(r.ieg_fy_raw) for r in members}
        disagree = len(texts) > 1
        for r in members:
            r.dup_count = len(members)
            if disagree:
                r.dup_disagree = True
                if r.status != QUARANTINE:
                    _quarantine(r, R_DUP)


def _categories(r):
    cats = set()
    if r.status == QUARANTINE:
        cats.add(r.reason)
    if r.aux_state == "invalid":
        cats.add(A_INVALID)
    if r.aux_state == "disagrees":
        cats.add(A_DISAGREE)
    if r.dup_count > 1:
        cats.add(C_DUP)
    if r.dup_disagree:
        cats.add(R_DUP)
    return cats


def _rule_id(status, reason):
    if status == ACCEPTED:
        return "FY-01"
    if status == ACCEPTED_WITH_AUX:
        return "FY-02"
    if status == FY_ONLY:
        return "FY-03"
    return RULE_FOR_QUARANTINE[reason]


def _row_cells(r):
    def num(value):
        return "" if value is None else str(value)

    return [
        r.project_id,
        r.page_file,
        str(r.index),
        _raw_text(r.as_of_raw),
        _raw_text(r.ieg_fy_raw),
        _raw_text(r.ieg_eval_raw),
        "true" if r.pno_found else "false",
        r.pno_key,
        _raw_text(r.pno_id_raw),
        _raw_text(r.pno_date_raw),
        _raw_text(r.pno_fy_raw),
        r.board_state,
        r.board_date.isoformat() if r.board_date is not None else "",
        r.board_reason or "",
        num(r.computed_fy),
        r.ieg_state,
        num(r.ieg_fy),
        r.ieg_reason or "",
        r.aux_state,
        num(r.resolved_fy),
        r.status,
        r.reason,
        _rule_id(r.status, r.reason),
        r.availability,
        r.availability_kind,
        str(r.dup_count),
        r.detail,
    ]


def _conflict_cells(r):
    return [
        r.project_id,
        r.page_file,
        str(r.index),
        r.status,
        r.reason,
        _rule_id(r.status, r.reason),
        " | ".join(sorted(r.categories)),
        _raw_text(r.ieg_fy_raw),
        _raw_text(r.pno_date_raw),
        _raw_text(r.pno_fy_raw),
        "" if r.computed_fy is None else str(r.computed_fy),
        "" if r.ieg_fy is None else str(r.ieg_fy),
        r.aux_state,
        r.detail,
        " | ".join(sorted({REVIEW_NOTE[c] for c in r.categories})),
        "unreviewed",
    ]


def _csv_bytes(header, rows):
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    for cells in rows:
        writer.writerow(cells)
    return buf.getvalue().encode("utf-8")


def _json_bytes(obj):
    return (json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _rules_bytes():
    lines = [
        "# IEG approval date and World Bank fiscal year rules",
        "",
        f"Schema version {SCHEMA_VERSION}. Written by `{TOOL_PATH}`. No outcome, rating, or other target field is read.",
        "",
        "## Fiscal year",
        "",
        "- `computed_wb_fy = year + 1 if month >= 7 else year`, from the approval date.",
        "- Fiscal year K covers [01 July (K-1), 01 July K). 30 June falls in the earlier FY; 1 July in the later one.",
        "- A bool is never a fiscal year.",
        f"- IEG `approval_fy` is valid only as a JSON integer or a string of exactly four ASCII digits, in [{MIN_FY}, FY of the snapshot as-of date]. Whitespace, signs, decimals and Unicode digits are refused.",
        "",
        "## Date",
        "",
        f"- Accepted forms: `YYYY-MM-DD`, and `YYYY-MM-DDTHH:MM:SS` with optional fractional seconds and a `Z` or `+00:00` zone.",
        "- A zoned time is read as its UTC calendar date. The host timezone is never read, so `TZ` cannot change a result.",
        "- A time with any other offset, or with no zone, is quarantined (FY-07). It is not converted.",
        "- An impossible calendar date, such as 29 February in a non-leap year, is quarantined (FY-06).",
        f"- The date must be on or after {MIN_DATE.isoformat()} and on or before the snapshot as-of date (FY-08).",
        "- An empty string or any non-string value is present but invalid (FY-06). It is never treated as absent.",
        "",
        "## Source precedence",
        "",
    ]
    for rule_id, status, text in RULES:
        lines.append(f"- **{rule_id}** ({status}): {text}")
    lines += [
        "",
        "## P&O approvalfy",
        "",
        "- It is an untrusted auxiliary value. It never decides the join and never acts as a fallback (C.7).",
        "- When the date and IEG agree, a P&O approvalfy that differs from the computed FY is recorded as `accepted_with_aux_warning`, not as a conflict of the approval date.",
        "- An invalid P&O approvalfy is recorded as an auxiliary warning. It does not quarantine the row.",
        "",
        "## Availability",
        "",
        "- With an approval date, availability is the approval date (`approval_date`).",
        "- With no approval date (`fy_only`), availability is 1 July of the FY before the resolved FY. This is the FY start, labelled `fy_start_NOT_an_approval_date`. It is not a real date and must not be read as one.",
        "",
        "## Duplicates",
        "",
        "- A repeated IEG project_id keeps every record, identified by page and index.",
        "- When the repeated records disagree on approval_fy, each is quarantined (FY-11). No record is preferred.",
        "",
        "## Scope",
        "",
        "- One row per IEG record. P&O records whose project_id has no IEG record are not written. They are counted in `approval_qa.json` as `pno_records_not_in_ieg`.",
        "- `evaluation_fy` is copied as a raw value for a future split locator. No decision uses it.",
        "",
        "## Quarantine reasons",
        "",
    ]
    for reason in sorted(RULE_FOR_QUARANTINE):
        lines.append(f"- `{reason}` ({RULE_FOR_QUARANTINE[reason]})")
    lines += [
        "",
        "## Conflict categories",
        "",
        "`approval_conflicts.csv` holds every row with a category: quarantined rows, rows with an auxiliary warning, and repeated IEG project ids. Each row starts `disposition = unreviewed`.",
        "",
    ]
    for cat in (A_DISAGREE, A_INVALID, C_DUP, R_DUP):
        lines.append(f"- `{cat}`: {REVIEW_NOTE[cat]}")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def _qa(rows, ieg_records, pno):
    status_counts = {s: 0 for s in STATUSES}
    reason_counts = defaultdict(int)
    board_state = defaultdict(int)
    board_reason = defaultdict(int)
    ieg_state = defaultdict(int)
    ieg_reason = defaultdict(int)
    aux_state = defaultdict(int)
    category = defaultdict(int)
    for r in rows:
        status_counts[r.status] += 1
        if r.reason:
            reason_counts[r.reason] += 1
        board_state[r.board_state] += 1
        if r.board_reason:
            board_reason[r.board_reason] += 1
        ieg_state[r.ieg_state] += 1
        if r.ieg_reason:
            ieg_reason[r.ieg_reason] += 1
        aux_state[r.aux_state] += 1
        for c in r.categories:
            category[c] += 1
    ieg_ids = {r.project_id for r in rows}
    repeated = defaultdict(int)
    for r in rows:
        repeated[r.project_id] += 1
    matched = ieg_ids & set(pno)
    conflict_rows = sum(1 for r in rows if r.categories)
    return {
        "schema_version": SCHEMA_VERSION,
        "counts": {
            "ieg_records_in": len(ieg_records),
            "rows_out": len(rows),
            "ieg_distinct_project_ids": len(ieg_ids),
            "ieg_project_ids_with_repeats": sum(1 for n in repeated.values() if n > 1),
            "ieg_rows_sharing_a_project_id": sum(n for n in repeated.values() if n > 1),
            "ieg_rows_without_pno_record": sum(1 for r in rows if not r.pno_found),
            "pno_records_in": len(pno),
            "pno_records_matched_to_ieg": len(matched),
            "pno_records_not_in_ieg": len(pno) - len(matched),
            "conflict_rows": conflict_rows,
        },
        "status_counts": status_counts,
        "reason_counts": dict(sorted(reason_counts.items())),
        "board_date_state_counts": dict(sorted(board_state.items())),
        "board_date_reason_counts": dict(sorted(board_reason.items())),
        "ieg_fy_state_counts": dict(sorted(ieg_state.items())),
        "ieg_fy_reason_counts": dict(sorted(ieg_reason.items())),
        "aux_state_counts": dict(sorted(aux_state.items())),
        "conflict_category_counts": dict(sorted(category.items())),
        "projection": {"ieg_fields": list(IEG_FIELDS), "pno_fields": list(PNO_FIELDS)},
    }


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_exclusive(path, data):
    with open(path, "xb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def _fsync_tree_bottom_up(root):
    dirs = []
    for dirpath, _dirnames, filenames in os.walk(root):
        if filenames:
            dirs.append(dirpath)
    for d in sorted(dirs, key=lambda p: p.count(os.sep), reverse=True):
        _fsync_dir(d)


def _publish(out, files):
    """Write into <out>.partial, verify, fsync, rename, then fsync the parent.

    Any failure before the rename leaves no final directory and deletes nothing.
    """
    partial = out.with_name(out.name + ".partial")
    os.mkdir(partial)
    for name in sorted(files):
        _write_exclusive(partial / name, files[name])
    for name in sorted(files):
        if (partial / name).read_bytes() != files[name]:
            raise OutputFailed(f"{name} does not read back as written")
    _fsync_tree_bottom_up(partial)
    os.rename(partial, out)
    try:
        _fsync_dir(out.parent)
    except OSError as exc:
        raise DurabilityUnconfirmed(
            f"{out} exists; the fsync of its parent failed ({exc.strerror or exc}), so durability is not confirmed"
        ) from exc


def build(snapshot, out, pins=DEFAULT_PINS):
    """Build the output directory. Return the sha256 of its manifest.json."""
    snapshot = Path(snapshot)
    out = Path(out)
    _check_destination(out)
    manifest_src, pages, as_of_text = _load_snapshot(snapshot, pins)
    as_of = parse_as_of(as_of_text)
    ieg_records = _parse_ieg(pages["ieg"], as_of_text)
    pno = _parse_pno(pages["projects"])

    totals = manifest_src["datasets"]
    if len(ieg_records) != totals["ieg"]["sum_records"]:
        raise SourceError("IEG record count differs from the manifest; records were lost or added")
    if len(pno) != totals["projects"]["sum_records"]:
        raise SourceError("P&O record count differs from the manifest; records were lost or added")

    rows = [_build_row(rec, pno.get(rec.project_id), as_of) for rec in ieg_records]
    _apply_duplicates(rows)
    for r in rows:
        r.categories = _categories(r)
    rows.sort(key=lambda r: (r.project_id, r.page_file, r.index))

    status_total = sum(1 for r in rows if r.status in STATUSES)
    locators = {(r.page_file, r.index) for r in rows}
    if len(rows) != len(ieg_records) or status_total != len(ieg_records) or len(locators) != len(ieg_records):
        raise SourceError("row accounting failed: rows in and out differ; nothing was written")

    qa = _qa(rows, ieg_records, pno)
    qa["snapshot"] = snapshot.name
    qa["snapshot_as_of"] = as_of.isoformat()
    qa["conservation"] = {
        "ieg_records_in": len(ieg_records),
        "rows_out": len(rows),
        "status_total": status_total,
        "distinct_locators": len(locators),
        "ok": True,
    }

    data_files = {
        OUT_ROWS: _csv_bytes(ROW_COLUMNS, [_row_cells(r) for r in rows]),
        OUT_CONFLICTS: _csv_bytes(
            CONFLICT_COLUMNS, [_conflict_cells(r) for r in rows if r.categories]
        ),
        OUT_QA: _json_bytes(qa),
        OUT_RULES: _rules_bytes(),
    }
    git_sha, git_dirty = _git_state(Path(__file__).resolve().parent)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "tool": {
            "path": TOOL_PATH,
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "git_sha": git_sha,
            "git_dirty": git_dirty,
            "python": platform.python_version(),
        },
        "inputs": {
            "snapshot": snapshot.name,
            "manifest_sha256": pins.manifest_sha256,
            "page_digest": pins.page_digest,
            "pages": {
                name: [{"file": rel, "sha256": sha} for rel, sha, _body in pages[name]]
                for name in ("ieg", "projects")
            },
            "records": {"ieg": len(ieg_records), "projects": len(pno)},
        },
        "outputs": {
            name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in sorted(data_files.items())
        },
    }
    manifest_bytes = _json_bytes(manifest)
    files = dict(data_files)
    files[OUT_MANIFEST] = manifest_bytes
    _publish(out, files)
    return hashlib.sha256(manifest_bytes).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description="IEG approval date <-> WB fiscal year QA, without label values.")
    parser.add_argument("--snapshot", required=True, type=Path, help="pinned snapshot directory (read-only)")
    parser.add_argument("--out", required=True, type=Path, help="new output directory; must not exist")
    args = parser.parse_args(argv)
    try:
        sha = build(args.snapshot, args.out)
    except DurabilityUnconfirmed as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except ToolError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"manifest sha256: {sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
