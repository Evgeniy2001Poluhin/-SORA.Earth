#!/usr/bin/env python3
"""IEG project -> World Bank country identity crosswalk, with no label values (step B1).

Reads three pinned inputs read-only and writes a new output directory: one row
per IEG record with its country resolved from two independent sources, the
conflicts that need a disposition, the aliases applied, the counts, and a manifest.

    snapshot   the World Bank snapshot 2026-10-15-sorted: IEG and P&O pages
    countries  the World Bank country registry snapshot (scripts/snapshot_wb_countries.py)
    oghist     the OGHIST historical income classification, read only for its
               wb_code and country_name columns; income_group is never read

Evidence. Name: the IEG country string, normalized, is looked up in a P&O
name dictionary built from the whole P&O corpus, or in the explicit alias table,
or in the fixed regional list. Code: the project's own P&O countrycode, found by
project_id. A P&O code becomes a country only through its ISO2 match in the
registry (iso2Code -> id). Codes the registry does not carry are never guessed
into a successor.

Standard library only. It opens no network connection and imports nothing from
the application. Every record is read through an explicit projection; asking a
record for any other key raises ProjectionError.

Dates are not used by this step. Nothing reads the clock except the manifest,
which records no timestamp. The derived files are a function of the pinned
inputs and the two input tables alone.

Usage:
    python scripts/ieg_country_crosswalk.py --snapshot DIR --countries DIR \
        --oghist DIR --aliases CSV [--regional CSV] --out DIR

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
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = "1"
RULE_VERSION = "1"
TOOL_PATH = "scripts/ieg_country_crosswalk.py"
HERE = Path(__file__).resolve().parent
DEFAULT_REGIONAL = HERE / "ieg_regional_names_v1.csv"

PINNED_SNAPSHOT_MANIFEST_SHA256 = "b3d43f8033367d79bb14ace692f8cada7068e98a3540fdfbbc0a1bea2505474c"
PINNED_PAGE_DIGEST = "960d776e8db25a59abcb3562e921c1643ace32466d71682c017b2912dc43d4f3"
PINNED_COUNTRIES_MANIFEST_SHA256 = "51dc057758c4b16a2d0e76bb62b9f063f7bbada9e48141b40aeb8161e4ffafdd"
PINNED_COUNTRIES_RAW_SHA256 = "99c199efc2fc2880c169eb611f5a32ba69adc0e225b83f54fe384a5d66ba2448"
PINNED_OGHIST_MANIFEST_SHA256 = "385d6e35e7db73d60b8a0d8ae407d54a870e106723acd175e69fd656dae6a4c2"

COUNTRIES_RAW_FILE = "raw/countries.json"
OGHIST_INCOME_FILE = "income_history.csv"
FORMER_SUFFIX = " (former)"

# The only keys this tool asks of a record. Anything else in a record is never read.
IEG_FIELDS = ("project_id", "country", "as_of_date")
PNO_FIELDS = ("id", "countrycode", "countryname")

# WB codes that are not ISO 3166 codes. Kept as WB identities with iso3 null, never shown as ISO3.
NON_ISO_WB_IDS = frozenset({"XKX"})

OUT_ROWS = "project_country_no_y.csv"
OUT_CONFLICTS = "country_conflicts.csv"
OUT_ALIASES = "country_aliases.csv"
OUT_QA = "country_qa.json"
OUT_MANIFEST = "manifest.json"
DATA_OUTPUTS = (OUT_ROWS, OUT_CONFLICTS, OUT_ALIASES, OUT_QA)

ALIAS_COLUMNS = (
    "raw_ieg_name", "normalized_name", "target_pno_name", "target_pno_code", "target_wb_code",
    "registry_name", "evidence_method", "evidence", "needs_external_source",
    "effective_start", "effective_end", "rule_id", "rule_version", "status",
)
REGIONAL_COLUMNS = ("kind", "key", "display", "class", "reason", "evidence", "rule_id", "status")
ALIAS_METHODS = ("truncation_40", "space_insensitive_equal", "token_permutation", "registry_name_and_code")
REVIEW_STATUSES = ("proposed", "reviewed")

# Statuses, one per IEG row.
ST_AGREE = "resolved_agree"
ST_NAME = "resolved_by_name"
ST_CODE = "resolved_by_code"
ST_CONFLICT = "conflict"
ST_REGION = "excluded_regional"
ST_QUAR = "quarantine"
STATUSES = (ST_AGREE, ST_NAME, ST_CODE, ST_CONFLICT, ST_REGION, ST_QUAR)

# Reasons.
R_NAME_CODE = "name_code_disagree"
R_DUP = "duplicate_project_id_country_disagreement"
R_REG_NAME = "regional_name"
R_REG_CODE = "regional_pno_code"
R_REG_AGG = "registry_aggregate_listed"
R_AGG_UNLISTED = "registry_aggregate_not_listed"
R_NOT_IN_REG = "pno_code_not_in_registry"
R_HISTORICAL = "historical_former_entity"
R_MULTI = "pno_code_multiple"
R_NEITHER = "neither_usable_source"

# Rule identifiers, one per decision branch. Alias and regional rows carry their own ids.
RULE_AGREE = "CW-01"
RULE_NAME = "CW-02"
RULE_CODE = "CW-03"
RULE_CONFLICT = "CW-04"
RULE_REGION = "CW-05"
RULE_QUAR = "CW-06"
RULE_DUP = "CW-07"

RULES = (
    (RULE_AGREE, ST_AGREE, "The name evidence and the P&O code evidence both resolve to the same P&O code, and that code is a registry country. The WB identity is the registry id of its iso2Code."),
    (RULE_NAME, ST_NAME, "The P&O record or its countrycode is missing. The IEG name resolves to a P&O code, exactly or through the alias table, and that code is a registry country."),
    (RULE_CODE, ST_CODE, "The IEG name does not resolve. The project's P&O countrycode is a registry country."),
    (RULE_CONFLICT, ST_CONFLICT, "The name evidence and the code evidence both resolve, and to different codes. Neither side is preferred. Quarantined for a disposition."),
    (RULE_REGION, ST_REGION, "Explicit regional entry: the IEG name, or the P&O code it resolves to, is in the fixed regional list. Excluded with its reason, not dropped."),
    (RULE_QUAR, ST_QUAR, "Any other case: a P&O code that the registry does not carry, a registry aggregate that is not on the fixed list, several codes in one record, or no usable evidence. The reason says which."),
    (RULE_DUP, ST_CONFLICT, "A repeated IEG project_id whose records give different country values. Every such record is a conflict; the duplicates are kept, not merged, and neither is preferred."),
)

# Classes of a code.
CLS_COUNTRY = "country"
CLS_REGION = "region"
CLS_REG_AGG = "registry_aggregate"
CLS_AGG_UNLISTED = "registry_aggregate_unlisted"
CLS_NOT_IN = "not_in_registry"
CLS_NONE = "none"
CLS_REGION_NAME = "region_name"

ABSENT_TEXT = "<missing>"


class ToolError(Exception):
    """A refusal reported as a message, without a traceback."""


class PinMismatch(ToolError):
    """An input does not match its pinned sha256 or digest."""


class SourceError(ToolError):
    """An input is malformed, or its records cannot be reconciled."""


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
    snapshot_manifest_sha256: str
    page_digest: str
    countries_manifest_sha256: str
    countries_raw_sha256: str
    oghist_manifest_sha256: str


DEFAULT_PINS = Pins(
    PINNED_SNAPSHOT_MANIFEST_SHA256,
    PINNED_PAGE_DIGEST,
    PINNED_COUNTRIES_MANIFEST_SHA256,
    PINNED_COUNTRIES_RAW_SHA256,
    PINNED_OGHIST_MANIFEST_SHA256,
)


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


# --- normalization --------------------------------------------------------------------


def norm(text):
    """NFKC, every run of Unicode whitespace to one space, casefold. Nothing else is removed."""
    return " ".join(unicodedata.normalize("NFKC", text).split()).casefold()


# --- strict input parsing ------------------------------------------------------------


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


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json_bytes(obj):
    return (json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _pno_list(value, what, where):
    """A P&O code or name field: absent or null is None; otherwise a list of strings."""
    if value is _ABSENT or value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SourceError(f"{where}: {what} is not a list of strings")
    return value


@dataclass
class IegRecord:
    page_file: str
    index: int
    project_id: str
    country: str


@dataclass
class PnoRecord:
    key: str
    codes: object  # None (absent or empty) or a list of strings
    names: object


def _load_snapshot(snapshot, pins):
    """Verify the snapshot pins and every page before a record is read. Return (manifest, pages by dataset, as_of)."""
    man_bytes = (snapshot / "manifest.json").read_bytes()
    if _sha(man_bytes) != pins.snapshot_manifest_sha256:
        raise PinMismatch("the snapshot manifest.json does not match the pinned sha256")
    manifest = _strict_json(man_bytes, "snapshot manifest.json")
    try:
        datasets = manifest["datasets"]
        listing = {name: datasets[name]["pages"] for name in ("ieg", "projects")}
        as_of_texts = datasets["ieg"]["as_of_dates"]
    except (KeyError, TypeError):
        raise SourceError("snapshot manifest.json lacks the dataset structure this tool reads") from None
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
                rel, want_sha, want_bytes = entry["file"], entry["sha256"], entry["bytes"]
            except (KeyError, TypeError):
                raise SourceError(f"{name}: a page entry lacks file, sha256 or bytes") from None
            if not isinstance(rel, str) or rel.split("/")[0] != name or not re.fullmatch(
                    rf"{name}/page-[0-9]{{5}}\.json", rel):
                raise SourceError(f"{name}: a manifest page path is not of the expected form")
            body = (snapshot / rel).read_bytes()
            got = _sha(body)
            if got != want_sha or len(body) != want_bytes:
                raise SourceError(f"{rel}: bytes or sha256 differ from the manifest")
            loaded.append((rel, body))
            digest_lines.append(f"{got}  {rel}")
        on_disk = {p for p in os.listdir(snapshot / name) if not p.startswith(".")}
        if on_disk != {rel.split("/")[1] for rel, _ in loaded}:
            raise SourceError(f"{name}/: files on disk differ from the manifest's page list")
        loaded.sort(key=lambda item: item[0])
        pages[name] = loaded

    digest = _sha("".join(line + "\n" for line in sorted(digest_lines)).encode("utf-8"))
    if digest != pins.page_digest:
        raise PinMismatch("the page digest does not match the pin")
    return manifest, pages, as_of_texts[0], digest_lines


def _parse_ieg(pages, as_of_text):
    records = []
    for rel, body in pages:
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
            country = fields["country"]
            if not isinstance(country, str) or country == "":
                raise SourceError(f"{rel}[{index}]: no usable country string")
            if fields["as_of_date"] != as_of_text:
                raise SourceError(f"{rel}[{index}]: as_of_date differs from the snapshot's")
            records.append(IegRecord(page_file=rel, index=index, project_id=pid, country=country))
    return records


def _parse_pno(pages):
    out = {}
    for rel, body in pages:
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
            codes = _pno_list(fields["countrycode"], "countrycode", rel)
            names = _pno_list(fields["countryname"], "countryname", rel)
            if bool(codes) != bool(names) or (codes and len(codes) != len(names)):
                raise SourceError(f"{rel}: countrycode and countryname are not present together")
            out[key] = PnoRecord(key=key, codes=codes or None, names=names or None)
    return out


# --- the country registry ----------------------------------------------------------------


@dataclass
class Registry:
    country_by_iso2: dict  # iso2Code -> (id, name)
    aggregate_by_iso2: dict  # iso2Code -> (id, name)


def _load_countries(directory, pins):
    man_bytes = (directory / "manifest.json").read_bytes()
    if _sha(man_bytes) != pins.countries_manifest_sha256:
        raise PinMismatch("the countries manifest.json does not match the pinned sha256")
    manifest = _strict_json(man_bytes, "countries manifest.json")
    try:
        raw_rel = manifest["raw"]["file"]
        raw_sha = manifest["raw"]["sha256"]
        raw_bytes = manifest["raw"]["bytes"]
        record_count = manifest["validated"]["record_count"]
    except (KeyError, TypeError):
        raise SourceError("countries manifest.json lacks the raw and validated structure") from None
    if raw_rel != COUNTRIES_RAW_FILE:
        raise SourceError("countries manifest.json names an unexpected raw file")
    if raw_sha != pins.countries_raw_sha256:
        raise PinMismatch("the countries raw sha256 does not match the pin")
    body = (directory / raw_rel).read_bytes()
    if _sha(body) != raw_sha or len(body) != raw_bytes:
        raise SourceError("the countries raw bytes differ from the manifest")

    doc = _strict_json(body, "countries raw")
    if not (isinstance(doc, list) and len(doc) == 2 and isinstance(doc[0], dict) and isinstance(doc[1], list)):
        raise SourceError("the countries response is not the documented [meta, records] shape")
    meta, records = doc
    if meta.get("pages") != 1 or meta.get("total") != len(records):
        raise SourceError("the countries meta does not match its records")
    if len(records) != record_count:
        raise SourceError("the countries record count differs from its manifest")

    by_iso2 = defaultdict(list)
    ids = set()
    countries, aggregates = {}, {}
    for index, rec in enumerate(records):
        if not isinstance(rec, dict):
            raise SourceError(f"countries record {index} is not an object")
        rid, iso2, name, region = rec.get("id"), rec.get("iso2Code"), rec.get("name"), rec.get("region")
        if not all(isinstance(v, str) and v for v in (rid, iso2, name)):
            raise SourceError(f"countries record {index} lacks a usable id, iso2Code or name")
        if not (isinstance(region, dict) and isinstance(region.get("value"), str)):
            raise SourceError(f"countries record {index} lacks a region value")
        if rid in ids:
            raise SourceError(f"countries id {rid!r} appears more than once")
        ids.add(rid)
        by_iso2[iso2].append(rid)
        if region["value"] == "Aggregates":
            aggregates[iso2] = (rid, name)
        else:
            countries[iso2] = (rid, name)
    if any(len(v) > 1 for v in by_iso2.values()):
        raise SourceError("an iso2Code is shared by two registry records; the lookup would be ambiguous")
    if set(countries) & set(aggregates):
        raise SourceError("an iso2Code is both a country and an aggregate")
    return Registry(country_by_iso2=countries, aggregate_by_iso2=aggregates), manifest


# --- OGHIST: identity columns only ---------------------------------------------------------


@dataclass
class Oghist:
    wb_codes: frozenset
    former_codes: frozenset
    income_sha256: str


def _load_oghist(directory, pins):
    man_bytes = (directory / "manifest.json").read_bytes()
    if _sha(man_bytes) != pins.oghist_manifest_sha256:
        raise PinMismatch("the OGHIST manifest.json does not match the pinned sha256")
    manifest = _strict_json(man_bytes, "OGHIST manifest.json")
    try:
        expected = manifest["outputs"][OGHIST_INCOME_FILE]
        want_sha, want_bytes = expected["sha256"], expected["bytes"]
    except (KeyError, TypeError):
        raise SourceError("OGHIST manifest.json does not list income_history.csv") from None
    body = (directory / OGHIST_INCOME_FILE).read_bytes()
    if _sha(body) != want_sha or len(body) != want_bytes:
        raise SourceError("income_history.csv differs from the OGHIST manifest")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise SourceError("income_history.csv is not UTF-8") from None
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader, None)
    if header is None or "wb_code" not in header or "country_name" not in header:
        raise SourceError("income_history.csv lacks the wb_code or country_name column")
    i_code, i_name = header.index("wb_code"), header.index("country_name")
    codes, former = set(), set()
    for row in reader:
        if len(row) <= max(i_code, i_name):
            raise SourceError("an income_history.csv row is shorter than its header")
        code = row[i_code]
        if code == "":
            continue
        codes.add(code)
        if row[i_name].endswith(FORMER_SUFFIX):
            former.add(code)
    return Oghist(frozenset(codes), frozenset(former), want_sha)


# --- explicit tables --------------------------------------------------------------------


@dataclass(frozen=True)
class Alias:
    raw: str
    normalized: str
    target_name: str
    target_code: str
    target_wb: str
    registry_name: str
    method: str
    evidence: str
    needs_external: bool
    rule_id: str
    rule_version: str
    status: str


@dataclass(frozen=True)
class RegionalEntry:
    kind: str  # "pno_code" or "name"
    key: str
    display: str
    cls: str  # "region" or "registry_aggregate"
    reason: str
    evidence: str
    rule_id: str
    status: str


def _read_csv_table(path, columns):
    try:
        text = Path(path).read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise SourceError(f"{path}: not UTF-8") from None
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if tuple(reader.fieldnames or ()) != columns:
        raise SourceError(f"{path}: the header is not the expected column list")
    rows = []
    for number, row in enumerate(reader, start=2):
        if None in row or any(value is None for value in row.values()):
            raise SourceError(f"{path} line {number}: wrong number of fields")
        rows.append(row)
    return rows, _sha(text.encode("utf-8"))


def _load_regional(path, aliases_norm, names_in_corpus, pno_names_by_code, pno_name_norms, registry):
    rows, sha = _read_csv_table(path, REGIONAL_COLUMNS)
    code_entries, name_entries = {}, {}
    seen_rules = set()
    for row in rows:
        if row["status"] not in REVIEW_STATUSES:
            raise SourceError(f"regional entry {row['rule_id']} has an unknown status")
        if row["rule_id"] in seen_rules:
            raise SourceError(f"regional rule_id {row['rule_id']} appears more than once")
        seen_rules.add(row["rule_id"])
        entry = RegionalEntry(row["kind"], row["key"], row["display"], row["class"], row["reason"],
                              row["evidence"], row["rule_id"], row["status"])
        if entry.kind == "pno_code":
            if entry.key in code_entries:
                raise SourceError(f"regional code {entry.key!r} appears more than once")
            if entry.key not in pno_names_by_code:
                raise SourceError(f"regional code {entry.key!r} does not occur in the P&O corpus")
            if pno_names_by_code[entry.key] != entry.display:
                raise SourceError(f"regional code {entry.key!r} is displayed under a name the corpus does not give it")
            if entry.key in registry.country_by_iso2:
                raise SourceError(f"regional code {entry.key!r} is a registry country; a country is not a region")
            if entry.cls == "registry_aggregate":
                if entry.key not in registry.aggregate_by_iso2:
                    raise SourceError(f"regional code {entry.key!r} is listed as an aggregate but the registry has none")
            elif entry.cls == "region":
                if entry.key in registry.aggregate_by_iso2:
                    raise SourceError(f"regional code {entry.key!r} is a registry aggregate; list it as registry_aggregate")
            else:
                raise SourceError(f"regional code {entry.key!r} has an unknown class")
            code_entries[entry.key] = entry
        elif entry.kind == "name":
            key = norm(entry.key)
            if key in name_entries:
                raise SourceError(f"regional name {entry.key!r} appears more than once")
            if key in pno_name_norms:
                raise SourceError(f"regional name {entry.key!r} is in the P&O name dictionary; list it by code")
            if key in aliases_norm:
                raise SourceError(f"regional name {entry.key!r} is also an alias: conflicting rules")
            if key not in names_in_corpus:
                raise SourceError(f"regional name {entry.key!r} does not occur in the IEG corpus")
            if entry.cls != "region":
                raise SourceError(f"regional name {entry.key!r} must have class region")
            name_entries[key] = entry
        else:
            raise SourceError(f"regional entry {entry.rule_id} has an unknown kind")
    return code_entries, name_entries, sha


def _load_aliases(path, name_dict, registry, pno_name_by_code):
    rows, sha = _read_csv_table(path, ALIAS_COLUMNS)
    aliases = {}
    seen_rules = set()
    for row in rows:
        if row["rule_id"] in seen_rules:
            raise SourceError(f"alias rule_id {row['rule_id']} appears more than once")
        seen_rules.add(row["rule_id"])
        raw = row["raw_ieg_name"]
        key = norm(raw)
        if row["normalized_name"] != key:
            raise SourceError(f"alias {row['rule_id']}: normalized_name is not the normalization of the raw name")
        if key in name_dict:
            raise SourceError(f"alias {row['rule_id']}: the name is an exact P&O name; aliases hold only non-exact names")
        if key in aliases:
            raise SourceError(f"alias {row['rule_id']}: a second rule for the same name: conflicting alias rules")
        if row["effective_start"] or row["effective_end"]:
            raise SourceError(f"alias {row['rule_id']}: dated aliases need the approval date, which this step does not project")
        if row["status"] not in REVIEW_STATUSES:
            raise SourceError(f"alias {row['rule_id']} has an unknown status")
        if row["evidence_method"] not in ALIAS_METHODS:
            raise SourceError(f"alias {row['rule_id']} has an unknown evidence method")
        code, target = row["target_pno_code"], row["target_pno_name"]
        if pno_name_by_code.get(code) != target:
            raise SourceError(f"alias {row['rule_id']}: the target P&O name and code are not one pair in the corpus")
        if code not in registry.country_by_iso2:
            raise SourceError(f"alias {row['rule_id']}: the target code is not a registry country")
        wb_id, wb_name = registry.country_by_iso2[code]
        if row["target_wb_code"] != wb_id or row["registry_name"] != wb_name:
            raise SourceError(f"alias {row['rule_id']}: the target WB id or registry name does not match the registry")
        method = row["evidence_method"]
        if method == "truncation_40":
            ok = raw[:40] == target and len(target) == 40 and raw != target
        elif method == "space_insensitive_equal":
            ok = "".join(raw.split()).casefold() == "".join(target.split()).casefold() and raw != target
        elif method == "token_permutation":
            ok = (sorted(re.findall(r"\w+", raw.casefold())) == sorted(re.findall(r"\w+", target.casefold()))
                  and raw != target)
        else:  # registry_name_and_code: no textual check; flagged for external verification
            ok = row["needs_external_source"] == "yes"
        if not ok:
            raise SourceError(f"alias {row['rule_id']}: its stated evidence method does not hold for these strings")
        aliases[key] = Alias(
            raw=raw, normalized=key, target_name=target, target_code=code, target_wb=wb_id,
            registry_name=wb_name, method=method, evidence=row["evidence"],
            needs_external=row["needs_external_source"] == "yes", rule_id=row["rule_id"],
            rule_version=row["rule_version"], status=row["status"],
        )
    return aliases, sha


# --- the P&O dictionary -----------------------------------------------------------------


def _build_dictionary(pno):
    """Exact name -> code, from records that carry exactly one name and one code. Refuse unless 1:1."""
    by_norm = defaultdict(set)  # normalized name -> {(raw name, code)}
    by_code = defaultdict(set)  # code -> {raw name}
    for rec in pno.values():
        if rec.codes is None or len(rec.codes) != 1:
            continue
        raw_name, code = rec.names[0], rec.codes[0]
        by_norm[norm(raw_name)].add((raw_name, code))
        by_code[code].add(raw_name)
    name_dict = {}
    pno_name_by_code = {}
    for key, pairs in by_norm.items():
        if len({raw for raw, _ in pairs}) != 1 or len({code for _, code in pairs}) != 1:
            raise SourceError(f"the P&O dictionary is not 1:1 under normalization for {key!r}")
        (raw, code), = pairs
        name_dict[key] = code
    for code, names in by_code.items():
        if len(names) != 1:
            raise SourceError(f"P&O code {code!r} carries more than one name")
        pno_name_by_code[code] = next(iter(names))
    if len(set(name_dict.values())) != len(name_dict):
        raise SourceError("two P&O names share one code; the dictionary is not 1:1")
    return name_dict, pno_name_by_code


# --- decisions ----------------------------------------------------------------------------


@dataclass
class Context:
    registry: Registry
    oghist: Oghist
    name_dict: dict
    pno_name_by_code: dict
    aliases: dict
    regional_codes: dict
    regional_names: dict


def _class(ctx, code):
    if code is None:
        return CLS_NONE
    if code in ctx.regional_codes:
        return CLS_REG_AGG if ctx.regional_codes[code].cls == "registry_aggregate" else CLS_REGION
    if code in ctx.registry.country_by_iso2:
        return CLS_COUNTRY
    if code in ctx.registry.aggregate_by_iso2:
        return CLS_AGG_UNLISTED
    return CLS_NOT_IN


@dataclass
class Row:
    project_id: str
    page_file: str
    index: int
    country_raw: str
    country_norm: str
    pno_found: bool
    pno_names_raw: object
    pno_codes_raw: object
    code_state: str = "absent"
    code_value: object = None
    name_kind: str = "none"  # exact | alias | region_name | none
    name_code: object = None
    name_rule: str = "NONE"
    name_class: str = CLS_NONE
    code_class: str = CLS_NONE
    status: str = ""
    reason: str = ""
    rule_id: str = ""
    resolved_code: object = None
    wb_code: str = ""
    iso2: str = ""
    registry_name: str = ""
    oghist_present: str = ""
    non_iso: str = ""
    regional_entry: str = ""
    dup_count: int = 1
    dup_disagree: bool = False
    detail: str = ""


def _set_status(row, status, reason, rule):
    row.status, row.reason, row.rule_id = status, reason, rule
    row.resolved_code = None
    row.wb_code = row.iso2 = row.registry_name = row.oghist_present = row.non_iso = ""


def _quarantine(row, reason):
    _set_status(row, ST_QUAR, reason, RULE_QUAR)


def _conflict(row, reason, rule):
    _set_status(row, ST_CONFLICT, reason, rule)


def _excluded(row, reason, rule_entry):
    _set_status(row, ST_REGION, reason, RULE_REGION)
    row.regional_entry = rule_entry


def _single(row, code, source, ctx):
    """Decide from one code, which is either the name's code or the project's code."""
    cls = _class(ctx, code)
    if cls == CLS_COUNTRY:
        wb_id, wb_name = ctx.registry.country_by_iso2[code]
        status, rule = {"agree": (ST_AGREE, RULE_AGREE), "name": (ST_NAME, RULE_NAME),
                        "code": (ST_CODE, RULE_CODE)}[source]
        _set_status(row, status, "", rule)
        row.resolved_code = code
        row.wb_code, row.iso2, row.registry_name = wb_id, code, wb_name
        row.oghist_present = "true" if wb_id in ctx.oghist.wb_codes else "false"
        row.non_iso = "true" if wb_id in NON_ISO_WB_IDS else "false"
        return
    if cls == CLS_REGION:
        _excluded(row, R_REG_CODE, ctx.regional_codes[code].rule_id)
    elif cls == CLS_REG_AGG:
        _excluded(row, R_REG_AGG, ctx.regional_codes[code].rule_id)
    elif cls == CLS_AGG_UNLISTED:
        _quarantine(row, R_AGG_UNLISTED)
    else:
        _quarantine(row, R_HISTORICAL if code in ctx.oghist.former_codes else R_NOT_IN_REG)


def _evaluate(row, ctx):
    if row.code_state == "multiple":
        _quarantine(row, R_MULTI)
        row.detail = "P&O countrycode holds several codes; none is chosen"
        return
    if row.name_kind in ("exact", "alias"):
        row.name_class = _class(ctx, row.name_code)
        if row.code_state == "present":
            row.code_class = _class(ctx, row.code_value)
            if row.name_code != row.code_value:
                _conflict(row, R_NAME_CODE, RULE_CONFLICT)
                return
            _single(row, row.name_code, "agree", ctx)
            return
        _single(row, row.name_code, "name", ctx)
        return
    if row.name_kind == "region_name":
        row.name_class = CLS_REGION_NAME
        if row.code_state == "absent":
            _excluded(row, R_REG_NAME, ctx.regional_names[row.country_norm].rule_id)
            return
        row.code_class = _class(ctx, row.code_value)
        if row.code_class == CLS_COUNTRY:
            _conflict(row, R_NAME_CODE, RULE_CONFLICT)
            return
        if row.code_class in (CLS_REGION, CLS_REG_AGG):
            _excluded(row, R_REG_NAME, ctx.regional_names[row.country_norm].rule_id)
            return
        _single(row, row.code_value, "code", ctx)
        return
    # The name does not resolve.
    if row.code_state == "absent":
        _quarantine(row, R_NEITHER)
        return
    row.code_class = _class(ctx, row.code_value)
    _single(row, row.code_value, "code", ctx)


def _name_evidence(row, ctx):
    key = row.country_norm
    if key in ctx.regional_names:
        row.name_kind, row.name_code, row.name_rule = "region_name", None, ctx.regional_names[key].rule_id
    elif key in ctx.name_dict:
        row.name_kind, row.name_code, row.name_rule = "exact", ctx.name_dict[key], "NAME-EXACT"
    elif key in ctx.aliases:
        alias = ctx.aliases[key]
        row.name_kind, row.name_code, row.name_rule = "alias", alias.target_code, alias.rule_id
    else:
        row.name_kind, row.name_code, row.name_rule = "none", None, "NONE"


def _code_evidence(rec):
    if rec is None or rec.codes is None:
        return "absent", None
    if len(rec.codes) != 1:
        return "multiple", None
    return "present", rec.codes[0]


def _detail(row):
    name = f"name={row.name_kind}:{row.name_code or '-'}({row.name_class})"
    code = f"code={row.code_state}:{row.code_value or '-'}({row.code_class})"
    return f"{name}; {code}; decided by {row.rule_id}"


def _build_rows(ieg_records, pno, ctx):
    rows = []
    for rec in ieg_records:
        prec = pno.get(rec.project_id)
        row = Row(
            project_id=rec.project_id,
            page_file=rec.page_file,
            index=rec.index,
            country_raw=rec.country,
            country_norm=norm(rec.country),
            pno_found=prec is not None,
            pno_names_raw=_ABSENT if prec is None or prec.names is None else prec.names,
            pno_codes_raw=_ABSENT if prec is None or prec.codes is None else prec.codes,
        )
        row.code_state, row.code_value = _code_evidence(prec)
        _name_evidence(row, ctx)
        _evaluate(row, ctx)
        row.detail = _detail(row) if not row.detail else row.detail + "; " + _detail(row)
        rows.append(row)
    return rows


def _apply_duplicates(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row.project_id].append(row)
    for members in groups.values():
        if len(members) == 1:
            continue
        values = {json.dumps(r.country_raw, ensure_ascii=False) for r in members}
        disagree = len(values) > 1
        for row in members:
            row.dup_count = len(members)
            if disagree:
                row.dup_disagree = True
                _conflict(row, R_DUP, RULE_DUP)


def _raw_text(value):
    if value is _ABSENT:
        return ABSENT_TEXT
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _row_cells(r):
    return [
        r.project_id, r.page_file, str(r.index),
        json.dumps(r.country_raw, ensure_ascii=False), r.country_norm,
        "true" if r.pno_found else "false",
        _raw_text(r.pno_names_raw), _raw_text(r.pno_codes_raw),
        r.name_kind, r.name_rule, r.name_code or "", r.name_class,
        r.code_state, r.code_value or "", r.code_class,
        r.status, r.reason, r.rule_id,
        r.resolved_code or "", r.wb_code, r.iso2, "WB" if r.wb_code else "", "", r.registry_name,
        r.oghist_present, r.non_iso,
        r.regional_entry, str(r.dup_count), r.detail,
    ]


ROW_COLUMNS = (
    "project_id", "ieg_page_file", "ieg_index",
    "ieg_country_raw", "ieg_country_norm",
    "pno_record_found", "pno_countryname_raw", "pno_countrycode_raw",
    "name_evidence", "name_evidence_rule", "name_pno_code", "name_class",
    "code_evidence", "code_pno_code", "code_class",
    "status", "reason", "rule_id",
    "resolved_pno_code", "wb_code", "iso2", "code_system", "iso3", "registry_name",
    "oghist_wb_code_present", "non_iso_wb_code",
    "regional_entry", "duplicate_project_id_count", "detail",
)

CONFLICT_COLUMNS = (
    "project_id", "ieg_page_file", "ieg_index", "reason", "rule_id", "ieg_country_raw",
    "name_pno_code", "name_class", "name_wb_code",
    "code_pno_code", "code_class", "code_wb_code",
    "possible_resolutions", "disposition",
)

_RESOLUTIONS = {
    R_NAME_CODE: "The IEG country and the project's P&O countrycode name different countries. Check the original project record for its country. Neither side is preferred here.",
    R_DUP: "The IEG snapshot repeats this project_id with different country values. Check the source record for each row. Neither is preferred here.",
}


def _conflict_cells(ctx, r):
    name_wb = ctx.registry.country_by_iso2[r.name_code][0] if r.name_class == CLS_COUNTRY else ""
    code_wb = ctx.registry.country_by_iso2[r.code_value][0] if r.code_class == CLS_COUNTRY else ""
    return [
        r.project_id, r.page_file, str(r.index), r.reason, r.rule_id,
        json.dumps(r.country_raw, ensure_ascii=False),
        r.name_code or "", r.name_class, name_wb,
        r.code_value or "", r.code_class, code_wb,
        _RESOLUTIONS[r.reason], "unreviewed",
    ]


ALIAS_OUT_COLUMNS = ALIAS_COLUMNS[:-1] + ("applied_rows", "status")


def _alias_cells(alias, applied):
    return [
        alias.raw, alias.normalized, alias.target_name, alias.target_code, alias.target_wb,
        alias.registry_name, alias.method, alias.evidence,
        "yes" if alias.needs_external else "no", "", "", alias.rule_id, alias.rule_version,
        str(applied), alias.status,
    ]


def _csv_bytes(header, rows):
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    for cells in rows:
        writer.writerow(cells)
    return buf.getvalue().encode("utf-8")


def _rules_list():
    return [{"rule_id": rid, "status": status, "text": text} for rid, status, text in RULES]


def _qa(rows, ieg_records, pno, ctx):
    status_counts = {s: 0 for s in STATUSES}
    reason_counts = defaultdict(int)
    name_kind = defaultdict(int)
    code_state = defaultdict(int)
    excluded_entries = defaultdict(int)
    oghist_absent_codes = defaultdict(int)
    for r in rows:
        status_counts[r.status] += 1
        if r.reason:
            reason_counts[f"{r.status}:{r.reason}"] += 1
        name_kind[r.name_kind] += 1
        code_state[r.code_state] += 1
        if r.status == ST_REGION and r.regional_entry:
            excluded_entries[r.regional_entry] += 1
        if r.status in (ST_AGREE, ST_NAME, ST_CODE) and r.oghist_present == "false":
            oghist_absent_codes[r.wb_code] += 1
    repeated = defaultdict(int)
    for r in rows:
        repeated[r.project_id] += 1
    matched = {r.project_id for r in rows if r.pno_found}
    resolved_wb = {r.wb_code for r in rows if r.wb_code}
    alias_used = defaultdict(int)
    for r in rows:
        if r.name_kind == "alias":
            alias_used[r.name_rule] += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "counts": {
            "ieg_records_in": len(ieg_records),
            "rows_out": len(rows),
            "ieg_distinct_project_ids": len(repeated),
            "ieg_project_ids_with_repeats": sum(1 for n in repeated.values() if n > 1),
            "ieg_rows_sharing_a_project_id": sum(n for n in repeated.values() if n > 1),
            "ieg_rows_without_pno_record": sum(1 for r in rows if not r.pno_found),
            "ieg_rows_with_pno_record_but_no_countrycode": sum(
                1 for r in rows if r.pno_found and r.code_state == "absent"),
            "pno_records_in": len(pno),
            "pno_records_matched_to_ieg": len(matched),
            "pno_records_not_in_ieg": len(pno) - len(matched),
            "resolved_wb_codes_distinct": len(resolved_wb),
        },
        "status_counts": status_counts,
        "reason_counts": dict(sorted(reason_counts.items())),
        "name_evidence_counts": dict(sorted(name_kind.items())),
        "code_evidence_counts": dict(sorted(code_state.items())),
        "regional_entry_counts": dict(sorted(excluded_entries.items())),
        "alias_rows_used": dict(sorted(alias_used.items())),
        "oghist": {
            "wb_codes_in_income_history": len(ctx.oghist.wb_codes),
            "former_entity_codes": sorted(ctx.oghist.former_codes),
            "resolved_rows_wb_code_absent_from_oghist": sum(
                1 for r in rows if r.oghist_present == "false"),
            "resolved_wb_codes_absent_from_oghist": sorted(oghist_absent_codes),
        },
        "non_iso_resolved_rows": sum(1 for r in rows if r.non_iso == "true"),
        "projection": {"ieg_fields": list(IEG_FIELDS), "pno_fields": list(PNO_FIELDS)},
    }


# --- output protocol ----------------------------------------------------------------------


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
    """Write into <out>.partial, verify it reads back, fsync, rename, then fsync the parent.

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


def _tool_identity():
    git_sha, git_dirty = _git_state(HERE)
    return {
        "path": TOOL_PATH,
        "script_sha256": _sha(Path(__file__).read_bytes()),
        "git_sha": git_sha,
        "git_dirty": git_dirty,
        "python": platform.python_version(),
    }


# --- build ----------------------------------------------------------------------------------


def build(snapshot, countries, oghist, aliases_path, out, *, regional_path=None, pins=None):
    """Build the output directory. Return the sha256 of its manifest.json.

    The defaults are read when the call runs, so a caller can replace DEFAULT_PINS or DEFAULT_REGIONAL.
    """
    snapshot, countries, oghist = Path(snapshot), Path(countries), Path(oghist)
    aliases_path, out = Path(aliases_path), Path(out)
    regional_path = DEFAULT_REGIONAL if regional_path is None else Path(regional_path)
    pins = DEFAULT_PINS if pins is None else pins
    _check_destination(out)

    snap_manifest, pages, as_of_text, digest_lines = _load_snapshot(snapshot, pins)
    ieg_records = _parse_ieg(pages["ieg"], as_of_text)
    pno = _parse_pno(pages["projects"])
    totals = snap_manifest["datasets"]
    if len(ieg_records) != totals["ieg"]["sum_records"]:
        raise SourceError("IEG record count differs from the manifest; records were lost or added")
    if len(pno) != totals["projects"]["sum_records"]:
        raise SourceError("P&O record count differs from the manifest; records were lost or added")

    registry, countries_manifest = _load_countries(countries, pins)
    oghist_info = _load_oghist(oghist, pins)

    name_dict, pno_name_by_code = _build_dictionary(pno)
    names_in_corpus = {norm(r.country) for r in ieg_records}
    pno_name_norms = set(name_dict)
    aliases, aliases_sha = _load_aliases(aliases_path, name_dict, registry, pno_name_by_code)
    regional_codes, regional_names, regional_sha = _load_regional(
        regional_path, set(aliases), names_in_corpus, pno_name_by_code, pno_name_norms, registry)
    ctx = Context(registry=registry, oghist=oghist_info, name_dict=name_dict,
                  pno_name_by_code=pno_name_by_code, aliases=aliases,
                  regional_codes=regional_codes, regional_names=regional_names)

    rows = _build_rows(ieg_records, pno, ctx)
    _apply_duplicates(rows)
    rows.sort(key=lambda r: (r.project_id, r.page_file, r.index))

    status_total = sum(1 for r in rows if r.status in STATUSES)
    locators = {(r.page_file, r.index) for r in rows}
    if len(rows) != len(ieg_records) or status_total != len(ieg_records) or len(locators) != len(ieg_records):
        raise SourceError("row accounting failed: rows in and out differ; nothing was written")

    applied = defaultdict(int)
    for r in rows:
        if r.name_kind == "alias":
            applied[r.name_rule] += 1
    applied_aliases = [a for a in sorted(aliases.values(), key=lambda a: a.rule_id) if applied[a.rule_id]]

    qa = _qa(rows, ieg_records, pno, ctx)
    qa["conservation"] = {
        "ieg_records_in": len(ieg_records),
        "rows_out": len(rows),
        "status_total": status_total,
        "distinct_locators": len(locators),
        "ok": True,
    }
    conflicts = [r for r in rows if r.status == ST_CONFLICT]
    data_files = {
        OUT_ROWS: _csv_bytes(ROW_COLUMNS, [_row_cells(r) for r in rows]),
        OUT_CONFLICTS: _csv_bytes(CONFLICT_COLUMNS, [_conflict_cells(ctx, r) for r in conflicts]),
        OUT_ALIASES: _csv_bytes(ALIAS_OUT_COLUMNS, [_alias_cells(a, applied[a.rule_id]) for a in applied_aliases]),
        OUT_QA: _json_bytes(qa),
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset": "ieg_country_crosswalk",
        "tool": _tool_identity(),
        "rule_version": RULE_VERSION,
        "inputs": {
            "snapshot": {
                "name": snapshot.name,
                "manifest_sha256": pins.snapshot_manifest_sha256,
                "page_digest": pins.page_digest,
                "pages": {name: [{"file": rel, "sha256": _sha(body)} for rel, body in pages[name]]
                          for name in ("ieg", "projects")},
                "records": {"ieg": len(ieg_records), "projects": len(pno)},
                "as_of_date": as_of_text,
            },
            "countries": {
                "name": countries.name,
                "manifest_sha256": pins.countries_manifest_sha256,
                "raw_file": COUNTRIES_RAW_FILE,
                "raw_sha256": pins.countries_raw_sha256,
                "record_count": countries_manifest["validated"]["record_count"],
                "country_count": countries_manifest["validated"]["country_count"],
                "aggregate_count": countries_manifest["validated"]["aggregate_count"],
            },
            "oghist": {
                "name": oghist.name,
                "manifest_sha256": pins.oghist_manifest_sha256,
                "income_history_sha256": oghist_info.income_sha256,
                "wb_codes": len(oghist_info.wb_codes),
                "former_entity_codes": sorted(oghist_info.former_codes),
                "columns_read": ["wb_code", "country_name"],
            },
            "aliases": {"file": aliases_path.name, "sha256": aliases_sha, "rows": len(aliases)},
            "regional": {"file": regional_path.name, "sha256": regional_sha,
                         "code_rows": len(regional_codes), "name_rows": len(regional_names)},
        },
        "projection": {"ieg_fields": list(IEG_FIELDS), "pno_fields": list(PNO_FIELDS)},
        "policy": {
            "iso3": "null everywhere: no ISO 3166 registry is pinned, so no code is shown as ISO3",
            "code_system": "WB for every resolved row; the WB id is the registry id of the P&O code's iso2Code",
            "dated_aliases": "not supported in this step: a dated alias needs the approval date, which is not projected",
            "legacy_codes": "P&O codes the registry does not carry are quarantined; no successor is assigned",
            "dup_policy": "repeated project_ids are kept, one row per IEG record; disagreement is a conflict",
        },
        "rules": _rules_list(),
        "outputs": {
            name: {"bytes": len(data), "sha256": _sha(data)}
            for name, data in sorted(data_files.items())
        },
    }
    manifest_bytes = _json_bytes(manifest)
    files = dict(data_files)
    files[OUT_MANIFEST] = manifest_bytes
    _publish(out, files)
    return _sha(manifest_bytes)


def main(argv=None):
    parser = argparse.ArgumentParser(description="IEG project -> WB country crosswalk, without label values.")
    parser.add_argument("--snapshot", required=True, type=Path, help="pinned snapshot 2026-10-15-sorted (read-only)")
    parser.add_argument("--countries", required=True, type=Path, help="pinned country registry directory (read-only)")
    parser.add_argument("--oghist", required=True, type=Path, help="pinned OGHIST r2 directory (read-only)")
    parser.add_argument("--aliases", required=True, type=Path, help="versioned alias table CSV")
    parser.add_argument("--regional", type=Path, default=DEFAULT_REGIONAL, help="fixed regional list CSV")
    parser.add_argument("--out", required=True, type=Path, help="new output directory; must not exist")
    args = parser.parse_args(argv)
    try:
        sha = build(args.snapshot, args.countries, args.oghist, args.aliases, args.out, regional_path=args.regional)
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
