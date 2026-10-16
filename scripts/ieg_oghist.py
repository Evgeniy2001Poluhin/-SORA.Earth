#!/usr/bin/env python3
"""World Bank historical country income classification (OGHIST): snapshot and lookup.

Fetches the official historical classification workbook over HTTPS, keeps the
bytes exactly as received, reads the workbook read-only with the standard
library, and writes a normalized table: one row per country and fiscal year
for which the file records an income group.

The script reads no outcome, rating, or project field; it has no such input.
It imports nothing from the application. It opens a network connection only in
fetch(), which runs only when --url is given. Formulas are never evaluated and
external links are never followed.

One run makes two GET requests: the source page (kept as evidence), then the
workbook (at most three attempts each). The source page must link the workbook's
URL, or the run is refused before the workbook is requested.

Output: a new directory. Nothing is overwritten and nothing is deleted.
    raw/<original workbook name>   the bytes as received
    source-page/<page file>        the source page as received
    fetch.json                     the fetch record: URLs, redirects, status,
                                   headers, sha256, time, attempts
    income_history.csv             normalized table
    income_schema_notes.md         sheet names, header and notes rows, quoted
    qa.json                        counts; no project data
    manifest.json                  source, hashes, tool identity, rules

Exit status: 0 published; 1 refused or failed before the rename, so no final
directory exists; 3 published, but the fsync of the parent directory failed,
so durability is not confirmed.
"""

import argparse
import csv
import hashlib
import http.client
import io
import json
import os
import platform
import posixpath
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "1"
TOOL_PATH = "scripts/ieg_oghist.py"

MAX_BYTES = 20 * 1024 * 1024  # size cap on the response body
MAX_PART_BYTES = 64 * 1024 * 1024  # size cap on one part of the workbook package once inflated
MAX_PACKAGE_BYTES = 256 * 1024 * 1024  # size cap on the sum of all parts once inflated
TIMEOUT_S = 60.0
ATTEMPTS = 3
RETRY_WAIT_S = (10.0, 30.0)  # the waits after attempts 1 and 2
USER_AGENT = (
    "sora-earth-ieg-research/1 (historical income classification snapshot; "
    "https://github.com/Evgeniy2001Poluhin/-SORA.Earth)"
)

RAW_DIR = "raw"
OUT_FETCH = "fetch.json"
OUT_INCOME = "income_history.csv"
OUT_NOTES = "income_schema_notes.md"
OUT_QA = "qa.json"
OUT_MANIFEST = "manifest.json"

GROUPS = ("LIC", "LMC", "UMC", "HIC")
PARTIAL_SUFFIX = ".partial"

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
CT_WORKBOOK = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
CT_MACRO = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_MAGIC = b"PK\x03\x04"
BOM = b"\xef\xbb\xbf"

_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_CELL_REF = re.compile(r"([A-Z]{1,3})([0-9]{1,7})")
_RESOURCE_ID = re.compile(r"/resources/([A-Za-z0-9]{1,32})(?:/|$)")


class ToolError(Exception):
    """A refusal, reported as a message without a traceback."""


class FetchError(ToolError):
    """The resource could not be fetched: an HTTP error, or the retry cap was used up."""


class OversizeError(ToolError):
    """The response body is larger than MAX_BYTES."""


class SourceRefused(ToolError):
    """The bytes are not a workbook this tool reads, or the workbook cannot be interpreted."""


class LayoutRefused(SourceRefused):
    """The workbook is readable, but its header or notes do not match a reviewed layout."""


class OutputRefused(ToolError):
    """The destination cannot be used. Nothing was written."""


class DurabilityUnconfirmed(ToolError):
    """The directory was renamed into place, but the parent fsync failed."""


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _q(ns, tag):
    return f"{{{ns}}}{tag}"


# --- transport -----------------------------------------------------------------------


@dataclass
class Response:
    status: int
    headers: dict  # lower-case names
    body: bytes  # empty unless status is 200
    final_url: str
    redirects: list  # [{"status", "from", "to"}] in the order followed


class _RedirectRecorder(urllib.request.HTTPRedirectHandler):
    """Follows redirects as urllib does, and keeps the chain for the fetch record."""

    def __init__(self):
        super().__init__()
        self.hops = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.hops.append({"status": code, "from": req.full_url, "to": newurl})
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _lower_headers(message):
    return {name.lower(): value for name, value in message.items()}


def _read_capped(resp):
    chunks = []
    total = 0
    while True:
        chunk = resp.read(1 << 16)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_BYTES:
            raise OversizeError(f"the response body exceeds {MAX_BYTES} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def _open_over_network(url, timeout=TIMEOUT_S):
    """One GET with certificate verification on. The only function that opens a socket."""
    recorder = _RedirectRecorder()
    opener = urllib.request.build_opener(
        recorder, urllib.request.HTTPSHandler(context=ssl.create_default_context())
    )
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream, */*;q=0.1"}
    )
    try:
        resp = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        return Response(exc.code, _lower_headers(exc.headers or {}), b"", exc.url or url, recorder.hops)
    with resp:
        headers = _lower_headers(resp.headers)
        declared = headers.get("content-length", "")
        if resp.status == 200 and declared.isdigit() and int(declared) > MAX_BYTES:
            raise OversizeError(f"Content-Length {declared} exceeds {MAX_BYTES} bytes")
        body = _read_capped(resp) if resp.status == 200 else b""
        return Response(resp.status, headers, body, resp.geturl(), recorder.hops)


def _check_https(url, what):
    if urllib.parse.urlsplit(url).scheme != "https":
        raise FetchError(f"{what} is not an https URL")


def fetch(url, *, transport=None, sleep=None):
    """Return (Response, attempts). At most ATTEMPTS attempts; only 429 and 5xx and transport errors retry.

    A 404 or any other non-retryable status fails at once. An oversize body fails at once.
    The transport and the sleep are looked up when the call is made, so a test can replace them.
    """
    transport = _open_over_network if transport is None else transport
    sleep = time.sleep if sleep is None else sleep
    _check_https(url, "the resource URL")
    attempts = []
    for attempt in range(1, ATTEMPTS + 1):
        started = _utc_now()
        try:
            resp = transport(url)
        except OversizeError:
            raise
        except (OSError, http.client.HTTPException) as exc:
            attempts.append({"attempt": attempt, "at_utc": started, "outcome": "transport_error",
                             "error": type(exc).__name__})
        else:
            attempts.append({"attempt": attempt, "at_utc": started, "outcome": "http", "status": resp.status})
            if resp.status == 200:
                for hop in resp.redirects:
                    _check_https(hop["to"], "a redirect target")
                _check_https(resp.final_url, "the final URL")
                return resp, attempts
            if not (resp.status == 429 or 500 <= resp.status <= 599):
                raise FetchError(f"HTTP {resp.status} is not retried")
        if attempt < ATTEMPTS:
            sleep(RETRY_WAIT_S[attempt - 1])
    raise FetchError(f"{ATTEMPTS} attempts failed; the last outcome was {attempts[-1]}")


_RFC5987 = re.compile(r"filename\*\s*=\s*UTF-8''([^;]+)", re.IGNORECASE)
_PLAIN_FILENAME = re.compile(r'filename\s*=\s*"?([^";]+)"?', re.IGNORECASE)


def raw_name(resp, requested_url):
    """Return (name, source). The name is the workbook's own name where the response gives one."""
    disposition = resp.headers.get("content-disposition", "")
    for pattern in (_RFC5987, _PLAIN_FILENAME):
        m = pattern.search(disposition)
        if m:
            candidate = urllib.parse.unquote(m.group(1).strip())
            if _SAFE_NAME.fullmatch(candidate) and "." in candidate:
                return candidate, "content-disposition"
    base = urllib.parse.unquote(urllib.parse.urlsplit(resp.final_url).path.rsplit("/", 1)[-1])
    if _SAFE_NAME.fullmatch(base) and "." in base:
        return base, "url-path"
    m = _RESOURCE_ID.search(urllib.parse.urlsplit(requested_url).path)
    resource = m.group(1) if m else "resource"
    return f"{resource}.bin", "fallback-resource-id"


# --- raw bytes and the fetch record ------------------------------------------------


def _fetch_record(*, page_url, requested_url, resp, attempts, body, name, name_source, fetched_at):
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "network",
        "page_url": page_url,
        "resource_url": requested_url,
        "final_url": resp.final_url,
        "redirects": resp.redirects,
        "http_status": resp.status,
        "content_type": resp.headers.get("content-type"),
        "content_length_header": resp.headers.get("content-length"),
        "content_disposition": resp.headers.get("content-disposition"),
        "etag": resp.headers.get("etag"),
        "last_modified": resp.headers.get("last-modified"),
        "name": name,
        "name_source": name_source,
        "bytes": len(body),
        "sha256": _sha256(body),
        "fetched_at_utc": fetched_at,
        "attempts": attempts,
        "tls_verified": True,
        "timeout_s": TIMEOUT_S,
        "size_cap_bytes": MAX_BYTES,
        "attempt_cap": ATTEMPTS,
        "user_agent": USER_AGENT,
    }


def _page_record(*, page_url, resource_url, resp, attempts, body, file_rel, fetched_at):
    return {
        "url": page_url,
        "searched_for": resource_url,
        "links_resource": True,
        "file": file_rel,
        "http_status": resp.status,
        "content_type": resp.headers.get("content-type"),
        "content_length_header": resp.headers.get("content-length"),
        "etag": resp.headers.get("etag"),
        "last_modified": resp.headers.get("last-modified"),
        "bytes": len(body),
        "sha256": _sha256(body),
        "fetched_at_utc": fetched_at,
        "attempts": attempts,
    }


def _page_file_name(page_url):
    """The page's own last path segment as an .html name, or a fixed name if it has none usable."""
    segment = urllib.parse.unquote(urllib.parse.urlsplit(page_url).path.rstrip("/").rsplit("/", 1)[-1])
    candidate = f"{segment}.html"
    return candidate if _SAFE_NAME.fullmatch(candidate) else "source-page.html"


def _check_page(resp, page_url, resource_url):
    """The page must be HTML and must link the workbook's URL. Refused otherwise, before the workbook is requested."""
    if "text/html" not in resp.headers.get("content-type", "").lower():
        raise SourceRefused(f"the source page {page_url} is not HTML")
    if resource_url.encode("utf-8") not in resp.body:
        raise SourceRefused(f"the source page does not link {resource_url}; the workbook is not requested")


# --- the workbook, read without evaluating anything ------------------------------------


def _classify_body(body):
    """Refuse everything that is not an .xlsx package before any parser sees it."""
    if not body:
        raise SourceRefused("the response body is empty")
    if body.startswith(OLE_MAGIC):
        raise SourceRefused("a legacy .xls (OLE compound file) is refused; only .xlsx is read")
    if body.startswith(ZIP_MAGIC):
        return
    head = body[:4096]
    if head.startswith(BOM):
        head = head[len(BOM):]
    head = head.lstrip(b" \t\r\n").lower()
    if head.startswith(b"<") or b"<html" in head or b"<!doctype" in head:
        raise SourceRefused("an HTML document was served where a workbook was expected")
    raise SourceRefused("the body is not an .xlsx package: there is no zip signature")


def _text_without_phonetic(element):
    """The text of a shared string or inline string. Phonetic runs (rPh) are not part of the value."""
    parts = []

    def walk(node):
        for child in node:
            if child.tag == _q(MAIN_NS, "rPh"):
                continue
            if child.tag == _q(MAIN_NS, "t"):
                parts.append(child.text or "")
            else:
                walk(child)

    walk(element)
    return "".join(parts)


def _column_index(letters):
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n


@dataclass(frozen=True)
class Cell:
    ref: str
    kind: str  # the t attribute: n, s, str, inlineStr, b, e, d
    text: str  # the stored value; for a shared string, the string
    formula: bool  # a formula is present. The cached value is never trusted.


@dataclass
class Sheet:
    name: str
    part: str
    cells: dict  # (row, column) -> Cell

    def cell(self, row, column):
        return self.cells.get((row, column))

    def max_row(self):
        return max((row for row, _ in self.cells), default=0)

    def max_column(self):
        return max((col for _, col in self.cells), default=0)

    def formula_count(self):
        return sum(1 for cell in self.cells.values() if cell.formula)


class Workbook:
    """A read-only view of an .xlsx package: sheet names, cell values, and formula flags."""

    def __init__(self, body):
        _classify_body(body)
        try:
            self._zip = zipfile.ZipFile(io.BytesIO(body))
        except Exception as exc:  # any failure to open the package is a refusal
            raise SourceRefused(f"the zip package is truncated or corrupt ({type(exc).__name__})") from None
        total = sum(info.file_size for info in self._zip.infolist())
        if total > MAX_PACKAGE_BYTES:
            raise SourceRefused(f"the package inflates to {total} bytes, above the {MAX_PACKAGE_BYTES} cap")
        try:
            bad = self._zip.testzip()
        except Exception as exc:
            raise SourceRefused(f"the zip package is corrupt ({type(exc).__name__})") from None
        if bad is not None:
            raise SourceRefused("the zip package fails its CRC check")
        self._names = set(self._zip.namelist())
        self._check_package()
        self._shared = self._read_shared_strings()
        self._sheets, self._hidden = self._read_sheet_index()

    def _read_part(self, name, limit=MAX_PART_BYTES):
        if name not in self._names:
            raise SourceRefused(f"the package has no part {name!r}")
        try:
            with self._zip.open(name) as fh:
                data = fh.read(limit + 1)
        except Exception as exc:
            raise SourceRefused(f"part {name!r} cannot be read ({type(exc).__name__})") from None
        if len(data) > limit:
            raise SourceRefused(f"part {name!r} is larger than {limit} bytes")
        return data

    def _parse_xml(self, name):
        data = self._read_part(name)
        if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
            raise SourceRefused(f"part {name!r} declares a DTD or entities; refused")
        try:
            return ET.fromstring(data)
        except ET.ParseError:
            raise SourceRefused(f"part {name!r} is not well-formed XML") from None

    def _check_package(self):
        if "xl/vbaProject.bin" in self._names:
            raise SourceRefused("the workbook carries a VBA project (macros); refused")
        ct = self._parse_xml("[Content_Types].xml")
        overrides = {o.get("PartName"): o.get("ContentType") for o in ct.iter(_q(CT_NS, "Override"))}
        main_type = overrides.get("/xl/workbook.xml")
        if main_type == CT_MACRO:
            raise SourceRefused("the workbook is macro-enabled (.xlsm); refused")
        if main_type != CT_WORKBOOK:
            raise SourceRefused("the workbook part is not declared as an .xlsx workbook")

    def _read_shared_strings(self):
        if "xl/sharedStrings.xml" not in self._names:
            return []
        root = self._parse_xml("xl/sharedStrings.xml")
        return [_text_without_phonetic(si) for si in root.findall(_q(MAIN_NS, "si"))]

    def _read_sheet_index(self):
        book = self._parse_xml("xl/workbook.xml")
        if book.tag != _q(MAIN_NS, "workbook"):
            raise SourceRefused("xl/workbook.xml is not a SpreadsheetML workbook")
        rels = self._parse_xml("xl/_rels/workbook.xml.rels")
        targets = {}
        for rel in rels.iter(_q(PKG_REL_NS, "Relationship")):
            if rel.get("TargetMode") == "External":
                continue
            targets[rel.get("Id")] = rel.get("Target")
        sheets = []
        hidden = []
        for sheet in book.iter(_q(MAIN_NS, "sheet")):
            name = sheet.get("name")
            rid = sheet.get(_q(REL_NS, "id"))
            target = targets.get(rid)
            if name is None or target is None:
                raise SourceRefused("a sheet in the workbook has no name or no target")
            part = target[1:] if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
            if part not in self._names:
                raise SourceRefused(f"sheet {name!r} points at a part that is not in the package")
            sheets.append((name, part))
            if sheet.get("state", "visible") != "visible":
                hidden.append(name)
        if not sheets:
            raise SourceRefused("the workbook has no sheets")
        return sheets, hidden

    def sheet_names(self):
        return [name for name, _part in self._sheets]

    def hidden_sheets(self):
        """Sheets the workbook marks hidden. They are read like any other sheet, and the notes say so."""
        return list(self._hidden)

    def sheet(self, name):
        matches = [part for sheet_name, part in self._sheets if sheet_name == name]
        if len(matches) != 1:
            raise SourceRefused(f"sheet {name!r} is absent or not unique")
        root = self._parse_xml(matches[0])
        if root.tag != _q(MAIN_NS, "worksheet"):
            raise SourceRefused(f"sheet {name!r} is not a worksheet")
        cells = {}
        for c in root.iter(_q(MAIN_NS, "c")):
            ref = c.get("r")
            m = _CELL_REF.fullmatch(ref or "")
            if m is None:
                raise SourceRefused(f"sheet {name!r} has a cell without a valid reference")
            key = (int(m.group(2)), _column_index(m.group(1)))
            if key in cells:
                raise SourceRefused(f"sheet {name!r} repeats cell {ref}")
            kind = c.get("t", "n")
            formula = c.find(_q(MAIN_NS, "f")) is not None
            value_el = c.find(_q(MAIN_NS, "v"))
            if kind == "s":
                raw_index = (value_el.text or "").strip() if value_el is not None else ""
                if not raw_index.isdigit() or int(raw_index) >= len(self._shared):
                    raise SourceRefused(f"sheet {name!r} cell {ref} points outside the shared strings")
                text = self._shared[int(raw_index)]
            elif kind == "inlineStr":
                is_el = c.find(_q(MAIN_NS, "is"))
                text = _text_without_phonetic(is_el) if is_el is not None else ""
            else:
                text = (value_el.text or "") if value_el is not None else ""
            cells[key] = Cell(ref=ref, kind=kind, text=text, formula=formula)
        return Sheet(name=name, part=matches[0], cells=cells)


# --- output protocol: exclusive create, verify, fsync, rename, fsync the parent --------


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


def _tool_identity():
    git_sha, git_dirty = _git_state(Path(__file__).resolve().parent)
    return {
        "path": TOOL_PATH,
        "script_sha256": _sha256(Path(__file__).read_bytes()),
        "git_sha": git_sha,
        "git_dirty": git_dirty,
        "python": platform.python_version(),
    }


def _check_destination(out):
    """Refuse before anything is fetched. The parent must exist; it is not created here."""
    parent = out.parent
    partial = out.with_name(out.name + PARTIAL_SUFFIX)
    if not parent.is_dir():
        raise OutputRefused(f"parent directory {parent} does not exist; this tool does not create it")
    if os.path.lexists(out):
        if out.is_symlink() or not out.is_dir() or any(out.iterdir()):
            raise OutputRefused(f"{out} exists and is not an empty directory")
    if os.path.lexists(partial):
        raise OutputRefused(f"{partial} exists; an earlier run did not finish, and it is not removed automatically")


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
    """fsync every directory that holds a file, deepest first, so each entry is durable before its parent."""
    dirs = []
    for dirpath, _dirnames, filenames in os.walk(root):
        if filenames:
            dirs.append(dirpath)
    for d in sorted(dirs, key=lambda p: p.count(os.sep), reverse=True):
        _fsync_dir(d)


def _json_bytes(obj):
    return (json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _csv_bytes(header, rows):
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    for cells in rows:
        writer.writerow(cells)
    return buf.getvalue().encode("utf-8")


# --- the pipeline -----------------------------------------------------------------------

INCOME_COLUMNS = (
    "wb_code",
    "code_system",
    "iso3",
    "country_name",
    "effective_fy",
    "effective_start",
    "effective_end",
    "income_group",
    "raw_cell_locator",
    "rule_id",
)


EXPECTED_SHEET = "Country Analytical History"
PARAMETERS_SHEET = "Parameters"
FY_LABEL = "Bank's fiscal year:"  # row 5, column B, as the file spells it
YEAR_LABEL = "Data for calendar year :"  # row 6, column B, as the file spells it
FY_ROW = 5
YEAR_ROW = 6
FIRST_DATA_ROW = 7
FIRST_VALUE_COLUMN = 3  # column C
PARAMETER_LABELS = ("Fiscal Yeay", "Publication Date", "GNI Year", "Term of Income Classification")
DOTDOT = ".."
STAR_CODE = "LM*"
GROUP_BY_CODE = {"L": "LIC", "LM": "LMC", "UM": "UMC", "H": "HIC"}
# The file's own category names on the Parameters sheet; the one-letter codes are their abbreviations.
CATEGORY_NAME_BY_CODE = {
    "L": "Low Income Threshold",
    "LM": "Lower Middle Income Range",
    "UM": "Upper Middle Income Range",
    "H": "High Income Threshold",
}
RULE_GROUP = "OGHIST-R1-GROUP"
RULE_FORMER = "OGHIST-R3-FORMER-ENTITY"
RULE_STAR = "OGHIST-R2-FOOTNOTE-STAR"
RULES = (
    (RULE_GROUP, "L, LM, UM, H in the country rows map to LIC, LMC, UMC, HIC. The fiscal year comes from row 5, checked against row 6 by the convention derived from the Parameters sheet."),
    (RULE_STAR, "LM* is the file's footnoted value for Yemen, Rep. The footnote (quoted in the notes) says that at the time there were two entities, Yemen, PDR (L) and Yemen, Arab Rep. (LM), and that combined they would have been LM. The value is kept as LMC and routed to review. This tool does not decide the predecessor and successor boundary."),
    (RULE_FORMER, "The entity's name carries '(former)'. Its code and group are kept as the file states them, with the entity's own boundary. A successor is not assigned. Routed to review."),
)
_ENTITY_CODE = re.compile(r"[A-Z]{3}[a-z]?")
_FY_HEADER = re.compile(r"FY([0-9]{2})")
_YEAR_HEADER = re.compile(r"[0-9]{4}")


def fiscal_year_of(d):
    """WB fiscal year K covers [1 July K-1, 1 July K). A date from 1 July on falls in the next fiscal year."""
    return d.year + (1 if d.month >= 7 else 0)


def fy_interval(fy):
    """The half-open interval [start, end) of fiscal year fy."""
    return date(fy - 1, 7, 1), date(fy, 7, 1)


def _col_letter(n):
    letters = ""
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _label(text):
    return " ".join(text.split())


def _text_at(sheet, row, column):
    cell = sheet.cell(row, column)
    return "" if cell is None else cell.text


def _formula_at(sheet, row, column):
    cell = sheet.cell(row, column)
    return cell is not None and cell.formula


def _read_parameters(wb):
    if PARAMETERS_SHEET not in wb.sheet_names():
        raise LayoutRefused(f"the workbook has no {PARAMETERS_SHEET!r} sheet, which states the year convention")
    sheet = wb.sheet(PARAMETERS_SHEET)
    found = {}
    for r in range(1, sheet.max_row() + 1):
        key_text = _text_at(sheet, r, 1)
        if key_text == "":
            continue
        if _formula_at(sheet, r, 1) or _formula_at(sheet, r, 2):
            raise SourceRefused(f"{PARAMETERS_SHEET}!A{r}: a formula; refused")
        key = _label(key_text)
        if key in found:
            raise LayoutRefused(f"the Parameters label {key!r} appears more than once")
        found[key] = _label(_text_at(sheet, r, 2))
    missing = [key for key in PARAMETER_LABELS if key not in found]
    if missing:
        raise LayoutRefused(f"the Parameters sheet lacks {missing}")
    return {key: found[key] for key in PARAMETER_LABELS}


def _year_convention(params):
    """Return the lag, in years, between a fiscal year and its GNI reference year, read from the file's notes.

    The Parameters sheet states one fiscal year three ways: its label, the year its
    term ends (1 July), and its GNI year. The publication date is 1 July of the year
    the fiscal year starts. The lag is the difference between the end year and the
    GNI year. Refused if the four statements disagree with one another.
    """
    label = re.fullmatch(r"FY([0-9]{2})", params["Fiscal Yeay"])
    term = re.fullmatch(r"until 1 July ([0-9]{4})", params["Term of Income Classification"])
    pub = re.fullmatch(r"\(([A-Za-z]+) ([0-9]{4})\)", params["Publication Date"])
    gni = re.fullmatch(r"[0-9]{4}", params["GNI Year"])
    if not (label and term and pub and gni):
        raise LayoutRefused("the Parameters values do not have the form this tool reads")
    end_year = int(term.group(1))
    if end_year % 100 != int(label.group(1)):
        raise LayoutRefused("the Parameters fiscal-year label disagrees with its term end date")
    if pub.group(1) != "July" or int(pub.group(2)) != end_year - 1:
        raise LayoutRefused("the Parameters publication date is not 1 July of the year the fiscal year starts")
    lag = end_year - int(gni.group(0))
    if not 1 <= lag <= 5:
        raise LayoutRefused(f"the Parameters GNI year implies a lag of {lag} years; refused")
    return lag, {
        "fiscal_year_label": params["Fiscal Yeay"],
        "publication_date": params["Publication Date"],
        "gni_year": params["GNI Year"],
        "term_of_classification": params["Term of Income Classification"],
    }


def _fy_columns(sheet, lag):
    """(column, fiscal year, calendar year, label) for each fiscal-year column, read from rows 5 and 6."""
    last = sheet.max_column()
    present = [c for c in range(FIRST_VALUE_COLUMN, last + 1)
               if _text_at(sheet, FY_ROW, c) != "" or _text_at(sheet, YEAR_ROW, c) != ""]
    if not present or present != list(range(FIRST_VALUE_COLUMN, FIRST_VALUE_COLUMN + len(present))):
        raise LayoutRefused("the fiscal-year header is not one unbroken block of columns from C")
    out = []
    for c in present:
        if _formula_at(sheet, FY_ROW, c) or _formula_at(sheet, YEAR_ROW, c):
            raise SourceRefused(f"header cell in column {_col_letter(c)} holds a formula; refused")
        label = _text_at(sheet, FY_ROW, c)
        year = _text_at(sheet, YEAR_ROW, c)
        fy_match = _FY_HEADER.fullmatch(label)
        if fy_match is None or _YEAR_HEADER.fullmatch(year) is None:
            raise LayoutRefused(f"column {_col_letter(c)}: row 5 {label!r} or row 6 {year!r} has an unexpected form")
        calendar = int(year)
        fy = calendar + lag
        if int(fy_match.group(1)) != fy % 100:
            raise LayoutRefused(f"column {_col_letter(c)}: {label} does not agree with calendar year {calendar}")
        out.append((c, fy, calendar, label))
    fys = [fy for _c, fy, _cal, _label_text in out]
    if fys != list(range(fys[0], fys[0] + len(fys))):
        raise LayoutRefused("the fiscal years in the header are not consecutive")
    return out


def _check_labels(sheet):
    if _label(_text_at(sheet, FY_ROW, 2)) != _label(FY_LABEL):
        raise LayoutRefused(f"B{FY_ROW} is {_text_at(sheet, FY_ROW, 2)!r}, not {FY_LABEL!r}")
    if _label(_text_at(sheet, YEAR_ROW, 2)) != _label(YEAR_LABEL):
        raise LayoutRefused(f"B{YEAR_ROW} is {_text_at(sheet, YEAR_ROW, 2)!r}, not {YEAR_LABEL!r}")


def _footnotes(sheet):
    """Rows with no code and a text in column B. These are notes, and they are quoted, never read as data."""
    out = []
    for r in range(FIRST_DATA_ROW, sheet.max_row() + 1):
        if _text_at(sheet, r, 1) == "" and _text_at(sheet, r, 2) != "":
            out.append((r, _text_at(sheet, r, 2)))
    return out


def _entities(sheet, last_column):
    """(code, name, row, values) per country row. Identical repeats are dropped; conflicting repeats refused."""
    by_code = {}
    dropped = []
    for r in range(FIRST_DATA_ROW, sheet.max_row() + 1):
        code = _text_at(sheet, r, 1)
        if code == "":
            continue
        if _formula_at(sheet, r, 1):
            raise SourceRefused(f"A{r}: a formula in a country code; refused")
        if _ENTITY_CODE.fullmatch(code) is None:
            raise SourceRefused(f"A{r}: {code!r} is not a World Bank country code")
        name = _text_at(sheet, r, 2)
        if name == "":
            raise SourceRefused(f"A{r}: code {code} has no country name")
        for c in range(last_column + 1, sheet.max_column() + 1):
            if _text_at(sheet, r, c) != "":
                raise LayoutRefused(f"row {r} holds a value in column {_col_letter(c)}, beyond the fiscal-year block")
        values = tuple(_text_at(sheet, r, c) for c in range(FIRST_VALUE_COLUMN, last_column + 1))
        for c in range(FIRST_VALUE_COLUMN, last_column + 1):
            if _formula_at(sheet, r, c):
                raise SourceRefused(f"{_col_letter(c)}{r}: a formula in an income cell; refused")
        entry = (code, name, r, values)
        if code in by_code:
            first = by_code[code]
            if (first[1], first[3]) != (name, values):
                raise SourceRefused(f"code {code} appears in rows {first[2]} and {r} with different values")
            dropped.append((code, r))
            continue
        by_code[code] = entry
    return [by_code[code] for code in sorted(by_code)], dropped


def derive_income(workbook, record):
    """Return (income_rows, qa, notes_text, parsing). Refuses anything it cannot read exactly as written."""
    if EXPECTED_SHEET not in workbook.sheet_names():
        raise LayoutRefused(f"the workbook has no sheet {EXPECTED_SHEET!r}; sheets: {workbook.sheet_names()}")
    params = _read_parameters(workbook)
    lag, convention_source = _year_convention(params)
    sheet = workbook.sheet(EXPECTED_SHEET)
    _check_labels(sheet)
    fy_cols = _fy_columns(sheet, lag)
    last_column = fy_cols[-1][0]
    footnotes = _footnotes(sheet)
    note_row, note_text = _publication_note(footnotes)
    star_noted = any(text.startswith("* ") for _r, text in footnotes)
    entities, dropped = _entities(sheet, last_column)

    rows = []
    by_fy = {fy: {"LIC": 0, "LMC": 0, "UMC": 0, "HIC": 0, "unknown_blank": 0, "unknown_dotdot": 0, "footnoted": 0}
             for _c, fy, _cal, _lbl in fy_cols}
    by_group = {g: 0 for g in GROUP_BY_CODE.values()}
    rule_counts = {RULE_GROUP: 0, RULE_STAR: 0, RULE_FORMER: 0}
    former = []
    star_refs = []
    unknown_blank = unknown_dotdot = cells_read = 0
    for code, name, row, _values in entities:
        is_former = "(former)" in name
        if is_former:
            former.append({"code": code, "name": name, "row": row})
        for column, fy, _cal, _lbl in fy_cols:
            cells_read += 1
            text = _text_at(sheet, row, column)
            locator = f"{EXPECTED_SHEET}!{_col_letter(column)}{row}"
            if text == "":
                unknown_blank += 1
                by_fy[fy]["unknown_blank"] += 1
                continue
            if text == DOTDOT:
                unknown_dotdot += 1
                by_fy[fy]["unknown_dotdot"] += 1
                continue
            if text == STAR_CODE:
                if not star_noted:
                    raise SourceRefused(f"{locator}: 'LM*' has no footnote in the file; refused")
                group, rule = "LMC", RULE_STAR
                by_fy[fy]["footnoted"] += 1
                star_refs.append(locator)
            elif text in GROUP_BY_CODE:
                group = GROUP_BY_CODE[text]
                rule = RULE_FORMER if is_former else RULE_GROUP
            else:
                raise SourceRefused(f"{locator}: {text!r} is not a documented income code; refused")
            start, end = fy_interval(fy)
            rows.append([code, "WB", "", name, str(fy), start.isoformat(), end.isoformat(), group, locator, rule])
            by_fy[fy][group] += 1
            by_group[group] += 1
            rule_counts[rule] += 1

    total_cells = len(entities) * len(fy_cols)
    if cells_read != total_cells or len(rows) + unknown_blank + unknown_dotdot != total_cells:
        raise SourceRefused("cell accounting failed: cells read and rows written do not add up; nothing was written")
    rows.sort(key=lambda row: (row[0], int(row[4])))
    codes_with_rows = {row[0] for row in rows}
    entities_without_rows = sum(1 for code, _n, _r, _v in entities if code not in codes_with_rows)
    suffixed = sorted(code for code, _n, _r, _v in entities if len(code) == 4)
    fys = [fy for _c, fy, _cal, _lbl in fy_cols]
    qa = {
        "schema_version": SCHEMA_VERSION,
        "sheet": EXPECTED_SHEET,
        "coverage": {
            "country_codes": len(entities),
            "fiscal_years": len(fys),
            "first_fiscal_year": fys[0],
            "last_fiscal_year": fys[-1],
            "cells": total_cells,
            "rows_out": len(rows),
            "unknown_cells": unknown_blank + unknown_dotdot,
        },
        "counts": {
            "rows_by_group": dict(sorted(by_group.items())),
            "rows_by_rule": dict(sorted(rule_counts.items())),
            "unknown_blank": unknown_blank,
            "unknown_dotdot": unknown_dotdot,
            "footnoted_values": len(star_refs),
            "former_entity_codes": len(former),
            "codes_with_no_rows": entities_without_rows,
            "identical_repeats_dropped": len(dropped),
            "conflicting_repeats": 0,
            "codes_outside_three_letters": suffixed,
            "formula_cells_read": 0,
        },
        "by_fiscal_year": {str(fy): by_fy[fy] for fy in fys},
        "review_for_B": {
            "footnoted_values": star_refs,
            "former_entities": former,
        },
    }
    parsing = {
        "sheet": EXPECTED_SHEET,
        "sheet_part": sheet.part,
        "publication_note_row": note_row,
        "fiscal_year_row": FY_ROW,
        "calendar_year_row": YEAR_ROW,
        "first_fiscal_year_column": _col_letter(FIRST_VALUE_COLUMN),
        "last_fiscal_year_column": _col_letter(last_column),
        "calendar_year_offset": lag,
        "year_convention_source": convention_source,
        "group_code_map": dict(GROUP_BY_CODE),
        "category_names": dict(CATEGORY_NAME_BY_CODE),
        "rules": [{"rule_id": rule_id, "text": text} for rule_id, text in RULES],
        "sheets_not_read": [name for name in workbook.sheet_names() if name not in (PARAMETERS_SHEET, EXPECTED_SHEET)],
        "hidden_sheets": sorted(workbook.hidden_sheets()),
    }
    notes_text = _notes_text(record, workbook, sheet, fy_cols, lag, params, footnotes, qa, parsing, former)
    return rows, qa, notes_text, parsing


def _publication_note(footnotes):
    """The file's own note that the classifications are set on 1 July. Refused if the file lacks it."""
    for r, text in footnotes:
        if "July 1" in text and "fiscal year" in text:
            return r, text
    raise LayoutRefused("the workbook has no note that the classifications are set on 1 July of each fiscal year")


def _notes_text(record, workbook, sheet, fy_cols, lag, params, footnotes, qa, parsing, former):
    def q(text):
        return "`" + text.replace("`", "'") + "`"

    note_row, note_text = _publication_note(footnotes)

    lines = [
        "# Income classification: schema notes",
        "",
        f"Schema version {SCHEMA_VERSION}, written by `{TOOL_PATH}` from the raw workbook. Every quotation is a cell's "
        "text, copied from the file. Nothing is typed in by hand.",
        "",
        "## Source",
        "",
        f"- Page: {record['page_url']}",
        f"- Resource: {record['resource_url']}",
        f"- Final URL after redirects: {record['final_url']}",
        f"- Raw file: `{RAW_DIR}/{record['name']}`, {record['bytes']} bytes, sha256 `{record['sha256']}`",
        f"- Content-Type: {record['content_type']}; Content-Disposition: {record['content_disposition']}",
        f"- Fetched (UTC): {record['fetched_at_utc']}",
        f"- Source page, saved as evidence: `{record['page']['file']}`, {record['page']['bytes']} bytes, sha256 "
        f"`{record['page']['sha256']}`, HTTP {record['page']['http_status']}, fetched (UTC) "
        f"{record['page']['fetched_at_utc']}. It was fetched by this run, one GET before the workbook GET, and it links "
        "the resource URL.",
        f"- Licence: not stated on the source page, which refers only to the website's terms of use. Not verified "
        "by this tool.",
        "",
        "## Sheets",
        "",
    ]
    read_role = {
        PARAMETERS_SHEET: "read: the year convention",
        EXPECTED_SHEET: "read: income group by country and fiscal year",
    }
    hidden = set(workbook.hidden_sheets())
    for name in workbook.sheet_names():
        marker = " (hidden in the workbook)" if name in hidden else ""
        lines.append(f"- {q(name)}{marker}: {read_role.get(name, 'not read')}")
    lines += ["", f"## {q(PARAMETERS_SHEET)} sheet, quoted", "", "| Label | Value |", "| --- | --- |"]
    p = workbook.sheet(PARAMETERS_SHEET)
    for r in range(1, p.max_row() + 1):
        key = _text_at(p, r, 1)
        if key and key != "Label":  # the sheet's own header row is the table's header
            lines.append(f"| {key} | {_text_at(p, r, 2)} |")
    lines += [
        "",
        "## Header rows of the income sheet, quoted",
        "",
        f"Row {FY_ROW} is {q(_text_at(sheet, FY_ROW, 2))}; row {YEAR_ROW} is {q(_text_at(sheet, YEAR_ROW, 2))}.",
        "",
        "| Column | Row 5 (fiscal year) | Row 6 (data for calendar year) | Fiscal year used |",
        "| --- | --- | --- | --- |",
    ]
    for column, fy, calendar, label in fy_cols:
        lines.append(f"| {_col_letter(column)} | {label} | {calendar} | {fy} |")
    lines += [
        "",
        "## Year convention",
        "",
        "- Fiscal year K covers 1 July of K-1 up to, but not including, 1 July of K (the end date is excluded).",
        f"- The file's note, row {note_row}, quoted in full: {q(note_text)}",
        f"- The Parameters sheet pins the convention: its label is {q(params['Fiscal Yeay'])}, its "
        f"{q(params['Term of Income Classification'])} term ends 1 July of that year, its "
        f"{q('Publication Date')} is {q(params['Publication Date'])}, and its {q('GNI Year')} is {q(params['GNI Year'])}.",
        f"- So the row 6 calendar year is the GNI reference year, and it is the fiscal year minus {lag}. This is the "
        f"offset the file states; it is not assumed to be +1 or +2. The tool checks it against all {len(fy_cols)} "
        "columns: the last two digits of each row 5 label must equal (calendar year + "
        f"{lag}) mod 100, and the fiscal years must run without a gap.",
        "- Boundary examples: the fiscal year of 2021-06-30 is FY2021 (interval 2020-07-01 to 2021-07-01, end excluded); "
        "the fiscal year of 2021-07-01 is FY2022.",
        "",
        "## Codes",
        "",
    ]
    for code, group in GROUP_BY_CODE.items():
        lines.append(f"- {q(code)} = {q(CATEGORY_NAME_BY_CODE[code])} in the Parameters sheet, written as {group}.")
    lines += [
        f"- {q('..')} means unknown: no row is written for it. A blank cell is unknown the same way.",
        f"- {q(STAR_CODE)} is a footnoted value; see the footnote below.",
        "",
        "## Footnotes, quoted",
        "",
    ]
    for r, text in footnotes:
        lines.append(f"- row {r}: {q(text)}")
    lines += [
        "",
        "## Entities kept as the file states them",
        "",
        f"- Codes that are not three capital letters: {', '.join(qa['counts']['codes_outside_three_letters']) or 'none'}.",
        f"- Former entities, routed to review (rule {RULE_FORMER}): "
        + ("; ".join(f"{e['code']} {q(e['name'])}" for e in former) or "none") + ".",
        "",
        "## Coverage",
        "",
        f"- Country codes: {qa['coverage']['country_codes']}; fiscal years: {qa['coverage']['fiscal_years']} "
        f"(FY{qa['coverage']['first_fiscal_year']} to FY{qa['coverage']['last_fiscal_year']}).",
        f"- Cells: {qa['coverage']['cells']}; rows written: {qa['coverage']['rows_out']}; unknown: "
        f"{qa['coverage']['unknown_cells']} ({qa['counts']['unknown_blank']} blank, "
        f"{qa['counts']['unknown_dotdot']} {q('..')}).",
        "",
        "## Rules",
        "",
    ]
    for rule_id, text in RULES:
        lines.append(f"- **{rule_id}**: {text}")
    lines.append("")
    return "\n".join(lines)



def build(out, *, url, page_url, transport=None, sleep=None):
    """Build the output directory. Return the sha256 of its manifest.json.

    The source page is fetched first, and the workbook is requested only if the page is
    HTML that links the workbook's URL. Nothing is written until both bodies are in hand.
    Once the .partial directory exists, a refusal leaves it in place with what was written
    so far, and deletes nothing.
    """
    out = Path(out)
    transport = _open_over_network if transport is None else transport
    sleep = time.sleep if sleep is None else sleep
    _check_destination(out)
    _check_https(page_url, "the page URL")

    page_resp, page_attempts = fetch(page_url, transport=transport, sleep=sleep)
    _check_page(page_resp, page_url, url)
    page_file = f"source-page/{_page_file_name(page_url)}"
    page_record = _page_record(
        page_url=page_url, resource_url=url, resp=page_resp, attempts=page_attempts, body=page_resp.body,
        file_rel=page_file, fetched_at=page_attempts[-1]["at_utc"],
    )

    resp, attempts = fetch(url, transport=transport, sleep=sleep)
    body = resp.body
    name, name_source = raw_name(resp, url)
    record = _fetch_record(
        page_url=page_url, requested_url=url, resp=resp, attempts=attempts, body=body,
        name=name, name_source=name_source, fetched_at=attempts[-1]["at_utc"],
    )
    record["page"] = page_record

    partial = out.with_name(out.name + PARTIAL_SUFFIX)
    try:
        os.mkdir(partial)
    except FileExistsError:
        raise OutputRefused(f"{partial} exists; an earlier run did not finish, and it is not removed automatically") from None
    (partial / RAW_DIR).mkdir()
    (partial / "source-page").mkdir()
    raw_rel = f"{RAW_DIR}/{name}"
    fetch_bytes = _json_bytes(record)
    written = {page_file: page_resp.body, raw_rel: body, OUT_FETCH: fetch_bytes}
    for rel in (page_file, raw_rel, OUT_FETCH):
        _write_exclusive(partial / rel, written[rel])

    workbook = Workbook(body)
    income_rows, qa, notes_text, parsing = derive_income(workbook, record)

    derived = {
        OUT_INCOME: _csv_bytes(INCOME_COLUMNS, income_rows),
        OUT_QA: _json_bytes(qa),
        OUT_NOTES: notes_text.encode("utf-8"),
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset": "world_bank_historical_income_classification",
        "tool": _tool_identity(),
        "python": platform.python_version(),
        "source": {
            "page_url": record["page_url"],
            "resource_url": record["resource_url"],
            "final_url": record["final_url"],
            "redirects": record["redirects"],
            "http_status": record["http_status"],
            "content_type": record["content_type"],
            "content_length_header": record["content_length_header"],
            "etag": record["etag"],
            "last_modified": record["last_modified"],
            "fetched_at_utc": record["fetched_at_utc"],
            "fetch_record": OUT_FETCH,
            "fetch_record_sha256": _sha256(fetch_bytes),
            "page": page_record,
            "licence": "not stated on the source page and not verified by this tool",
        },
        "raw": {"file": raw_rel, "sha256": _sha256(body), "bytes": len(body)},
        "parsing": parsing,
        "coverage": qa["coverage"],
        "outputs": {
            name: {"bytes": len(data), "sha256": _sha256(data)}
            for name, data in sorted({**derived, OUT_FETCH: fetch_bytes, raw_rel: body, page_file: page_resp.body}.items())
        },
        "schema": {"income_groups": list(GROUPS), "income_columns": list(INCOME_COLUMNS)},
    }
    manifest_bytes = _json_bytes(manifest)

    expected = {**written, **derived, OUT_MANIFEST: manifest_bytes}
    for rel in sorted(derived) + [OUT_MANIFEST]:
        _write_exclusive(partial / rel, expected[rel])
    for rel, data in expected.items():
        if (partial / rel).read_bytes() != data:
            raise OutputRefused(f"{rel} does not read back as written")
    _fsync_tree_bottom_up(partial)
    os.rename(partial, out)
    try:
        _fsync_dir(out.parent)
    except OSError as exc:
        raise DurabilityUnconfirmed(
            f"{out} exists; the fsync of its parent failed ({exc.strerror or exc}), so durability is not confirmed"
        ) from exc
    return _sha256(manifest_bytes)


def main(argv=None):
    parser = argparse.ArgumentParser(description="World Bank historical income classification: snapshot and lookup.")
    parser.add_argument("--url", required=True, help="the resource URL, fetched over https")
    parser.add_argument("--page-url", required=True, help="the official page that links the resource")
    parser.add_argument("--out", required=True, type=Path, help="new output directory; must not exist")
    args = parser.parse_args(argv)
    try:
        sha = build(args.out, url=args.url, page_url=args.page_url)
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
