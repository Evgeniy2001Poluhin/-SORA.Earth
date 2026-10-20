#!/usr/bin/env python3
"""R1 target-only builder for the SORA.Earth IEG pilot.

Specification: R1-TARGET-BUILDER-SPEC-2026-10-20, sections 1-12 (later sections
prevail). For each structural candidate of the r2 receipt, the IEG records on
the candidate's maximum evaluation FY are read, and ieg_outcome_satisfactory is
assigned from their `outcome` value. No feature is read, no model is fitted and
the holdout is not opened.

Only this command line runs on real data:

    python scripts/ieg_target_r1.py --help
    python scripts/ieg_target_r1.py --real --r1-permit FILE --out DIR

Any other command line exits 3 before any data file is opened.

Exit codes: 0 ok; 3 refused before any data read (command line, permit, trust
file, pins, existing output folder); 4 blocked (only DIR.blocked/blocked_receipt.json
is written); 5 integrity error in an input.

The synthetic tests call validate_bytes(), build() and write_outputs() on
in-memory bytes; they never reach load_real().
"""

from __future__ import annotations

import csv
import dataclasses
import datetime as _dt
import functools
import hashlib
import io
import json
import os
import re
import resource
import shutil
import subprocess
import sys
import tarfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

RULE_VERSION = "1"
STATEMENT = "R1 target-only: ieg_outcome_satisfactory per structural candidate; not a model-quality result"
ROOT = "/Users/evgenijpoluhin/sora-research"
SNAPSHOT_AS_OF = _dt.date(2026, 10, 13)
SNAPSHOT_AS_OF_RAW = "13-Oct-2026"
WHITELIST = ("project_id", "evaluation_fy", "as_of_date", "outcome")
PAGE_KEYS = ("count", "data")
WINDOWS = ("development", "holdout")
DEVELOPMENT_MAX_EVAL_FY = 2019
HOLDOUT_MIN_EVAL_FY = 2020
HOLDOUT_MAX_EVAL_FY = 2026
OWNER_DECISION = "разрешаю R1"
DISPOSITION_REASON = {
    "T1": "outcome_missing",
    "T2": "outcome_not_rated",
    "T3": "outcome_unknown_literal",
    "T4": "latest_rating_conflict",
}
ABSENT_KINDS = ("missing", "null", "empty")
D1_STATEMENT = (
    "провал D1 разрешает только описательную интерпретацию; "
    "G_eff — эффективное число стран, не эффективное число проектов"
)

ROW_MAX = 16384          # one serialized CSV record, newline included (13.12.1)
JSON_MAX = 1048576       # one serialized JSON file (13.12.1)
INNER_BYTES = 65         # inner_sha256.txt: 64 hex digits and a newline
TAR_RECORDSIZE = 10240   # USTAR archives are padded to this (13.12.4)

def _at_boundary(fn):
    """The loader boundary raises fixed-message errors only. Detail (page names, values, paths) is dropped
    here and never kept in an exception argument; unexpected errors become integrity_error (13.1, review)."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (PinRefused, PermitRefused, OutputRefused, IntegrityError) as exc:
            raise type(exc)(str(exc)) from None
        except Exception:
            raise IntegrityError("integrity_error") from None
    return wrapper


PID_RE = re.compile(r"P[0-9]{6}")
EVAL_FY_RE = re.compile(r"[0-9]{4}")
AS_OF_RE = re.compile(r"([0-9]{2})-([A-Za-z]{3})-([0-9]{4})")
ISO_UTC_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
HEX64_RE = re.compile(r"[0-9a-f]{64}")
MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}

BUILDER_PATH = Path(__file__).resolve()
TRUST_PATH = BUILDER_PATH.with_name("ieg_target_r1_trust.json")

K_SNAP_MANIFEST = "snapshot/manifest.json"
K_STRUCT_MANIFEST = "structural/manifest.json"
K_VOCAB = "vocabulary.json"
K_D1 = "d1_policy.json"
STRUCT_FILES = ("structural_candidates.csv", "split_ids_development.txt", "split_ids_holdout.txt")
CANDIDATE_COLUMNS = ("project_id", "window", "evaluation_fy", "wb_code", "authoritative_records")


class PinRefused(RuntimeError):
    """An input does not match its pin, or a pin is not set. Refused before the outputs are written."""


class PermitRefused(PinRefused):
    """The R1 permit or the trust file does not allow a real run."""


class OutputRefused(RuntimeError):
    """The output folder exists, or a partial or blocked folder of that name exists."""


class IntegrityError(RuntimeError):
    """An input is internally inconsistent or holds a value the builder cannot place."""


@dataclass(frozen=True)
class Pins:
    snapshot_manifest_sha: str | None
    snapshot_page_digest: str | None
    structural_manifest_sha: str | None
    vocabulary_sha: str | None
    d1_policy_sha: str | None


# Real-mode constants. Only the pins that are None here are not yet signed or
# not yet supplied; while any of them is None, load_real() refuses before it
# opens a data file.
REAL_PINS = Pins(
    snapshot_manifest_sha="b3d43f8033367d79bb14ace692f8cada7068e98a3540fdfbbc0a1bea2505474c",
    snapshot_page_digest="960d776e8db25a59abcb3562e921c1643ace32466d71682c017b2912dc43d4f3",
    structural_manifest_sha="36b541dbf74f87762a292125ac454c981ac7381ddce05faae4b97514a2c53ef4",
    vocabulary_sha=None,
    d1_policy_sha=None,
)
REAL_SNAPSHOT_DIR = f"{ROOT}/worldbank-snapshots/2026-10-15-sorted"
# The directory of the r2 structural receipt is not given in the spec. It stays
# None, so a real run refuses until the owner supplies it with the permit.
REAL_STRUCTURAL_DIR = None
REAL_VOCABULARY_PATH = None
REAL_D1_PATH = None
# The owner's X25519 public key lives in R0 (13.5) and is pinned here (13.8). None until it is
# written into the signed R0; while it is None, a real run refuses before any page is read.
REAL_OWNER_PUBLIC_KEY_HEX = None


@dataclass(frozen=True)
class Vocabulary:
    values: Mapping[str, int]
    not_rated: frozenset


@dataclass(frozen=True)
class D1Policy:
    cohort: str
    min_g: int
    max_largest_share: float
    min_g_eff: float


@dataclass(frozen=True)
class SourceRecord:
    """One valid IEG record, reduced to the four whitelisted keys and the typed outcome."""
    page: str
    index: int
    project_id: str
    evaluation_fy: int
    outcome_present: bool
    outcome_json: str
    canon: tuple


@dataclass(frozen=True)
class Candidate:
    project_id: str
    window: str
    evaluation_fy: int
    wb_code: str
    records: tuple


@dataclass(frozen=True)
class Inputs:
    pins: Pins
    input_sha256: Mapping[str, str]
    candidates: tuple
    vocabulary: Vocabulary
    d1: D1Policy
    run_meta: Mapping = field(default_factory=dict)
    seal: "SealParams | None" = None


@dataclass(frozen=True)
class Result:
    status: str
    files: Mapping[str, bytes]
    sealed: Mapping[str, bytes]
    input_sha256: Mapping[str, str]
    run_meta: Mapping


# ---------------------------------------------------------------- helpers

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(obj) -> bytes:
    """13.12.2: sorted keys, compact separators, UTF-8. Over JSON_MAX is a field-limit refusal (exit 5)."""
    data = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(data) > JSON_MAX:
        raise IntegrityError(FIELD_LIMIT)
    return data


def _csv_record(values) -> bytes:
    """13.12.2: QUOTE_ALL, '\n' terminator. One record over ROW_MAX bytes is a field-limit refusal (exit 5)."""
    buf = io.StringIO()
    csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n").writerow(list(values))
    data = buf.getvalue().encode("utf-8")
    if len(data) > ROW_MAX:
        raise IntegrityError(FIELD_LIMIT)
    return data


def _csv_bytes(fields, rows) -> bytes:
    return b"".join([_csv_record(fields)] + [_csv_record([row.get(f, "") for f in fields]) for row in rows])


def _typed_json(canon: tuple) -> str:
    return json.dumps(list(canon), ensure_ascii=False, separators=(",", ":"))


def _canonical_sha(canon: tuple) -> str:
    return sha256_bytes(_typed_json(canon).encode("utf-8"))


def _type_label(canon: tuple) -> str:
    return "string" if canon[0] == "string" else f"nonstring:{canon[1]}"


def _projection_hook(pairs):
    """Keep only the whitelisted keys and the page-level count/data keys, as each object is built."""
    return {k: v for k, v in pairs if k in WHITELIST or k in PAGE_KEYS}


def canonical_outcome(present: bool, value) -> tuple:
    """Typed canonical value of one outcome. Case is kept; internal whitespace is collapsed."""
    if not present:
        return ("missing",)
    if value is None:
        return ("null",)
    if isinstance(value, str):
        text = " ".join(value.split())
        return ("empty",) if text == "" else ("string", text)
    if isinstance(value, bool):
        return ("nonstring", "bool")
    if isinstance(value, int):
        return ("nonstring", "int")
    if isinstance(value, float):
        return ("nonstring", "float")
    if isinstance(value, list):
        return ("nonstring", "list")
    if isinstance(value, dict):
        return ("nonstring", "object")
    return ("nonstring", "other")


def classify(canon: tuple, vocab: Vocabulary) -> tuple[str, int | None]:
    """('absent', None) | ('not_rated', None) | ('rated', 0/1) | ('unknown', None)."""
    if canon[0] in ABSENT_KINDS:
        return ("absent", None)
    if canon[0] == "string":
        if canon[1] in vocab.not_rated:
            return ("not_rated", None)
        if canon[1] in vocab.values:
            return ("rated", vocab.values[canon[1]])
    return ("unknown", None)


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


def _pin(actual: str, pinned: str | None, label: str) -> None:
    if pinned is None:
        raise PinRefused(f"{label}: no pin is set")
    if actual != pinned:
        raise PinRefused(f"{label}: does not match the pinned value")


def _blob(blobs: Mapping[str, bytes], key: str) -> bytes:
    if key not in blobs:
        raise IntegrityError("input is missing")
    return blobs[key]


def _parse_json(data: bytes, label: str, **kwargs):
    try:
        return json.loads(data.decode("utf-8"), **kwargs)
    except (UnicodeDecodeError, ValueError):
        # Fixed text: neither the parser's message (which can carry a value's size) nor the label is kept.
        raise IntegrityError("integrity_error") from None


def _safe_name(name) -> str:
    """A relative path inside the snapshot folder: no absolute path, no '..' and no empty part."""
    if not isinstance(name, str) or not name or name.startswith("/") or "\\" in name:
        raise IntegrityError("manifest names a file outside its folder")
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise IntegrityError("manifest names a file outside its folder")
    return name


def _ieg_block(manifest) -> dict:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("datasets"), dict):
        raise IntegrityError("snapshot manifest has no datasets")
    ieg = manifest["datasets"].get("ieg")
    if not isinstance(ieg, dict) or not isinstance(ieg.get("pages"), list) or not isinstance(ieg.get("api_total"), int):
        raise IntegrityError("snapshot manifest has no ieg dataset block")
    return ieg


def ieg_page_names(manifest_bytes: bytes) -> list:
    """File names of the ieg pages, in manifest order. Used to choose which pages to read."""
    ieg = _ieg_block(_parse_json(manifest_bytes, "snapshot manifest"))
    return [_safe_name(page.get("file")) for page in ieg["pages"]]


# ---------------------------------------------------------------- parsing and validation

def _valid_record(page: str, index: int, item: dict) -> SourceRecord | None:
    """A record with a valid project_id, as_of_date and evaluation_fy, else None (dropped)."""
    pid = item.get("project_id")
    if not (isinstance(pid, str) and PID_RE.fullmatch(pid)):
        return None
    if parse_as_of(item.get("as_of_date")) != SNAPSHOT_AS_OF:
        return None
    fy = parse_eval_fy(item.get("evaluation_fy"))
    if fy is None:
        return None
    present = "outcome" in item
    value = item.get("outcome")
    return SourceRecord(
        page=page,
        index=index,
        project_id=pid,
        evaluation_fy=fy,
        outcome_present=present,
        outcome_json=json.dumps(value, ensure_ascii=False, sort_keys=True) if present else "",
        canon=canonical_outcome(present, value),
    )


def _parse_vocabulary(data: bytes) -> Vocabulary:
    obj = _parse_json(data, "vocabulary")
    if not isinstance(obj, dict) or set(obj) != {"map", "not_rated"}:
        raise IntegrityError("vocabulary must hold exactly the keys map and not_rated")
    values = obj["map"]
    not_rated = obj["not_rated"]
    if not isinstance(values, dict) or not isinstance(not_rated, list):
        raise IntegrityError("vocabulary map must be an object and not_rated a list")
    for literal, value in values.items():
        if " ".join(literal.split()) != literal or literal == "":
            raise IntegrityError("vocabulary literal is not in canonical form")
        if isinstance(value, bool) or value not in (0, 1):
            raise IntegrityError("vocabulary value for a literal must be 1 or 0")
    for literal in not_rated:
        if not isinstance(literal, str) or " ".join(literal.split()) != literal or literal == "":
            raise IntegrityError("vocabulary not_rated entry is not in canonical form")
        if literal in values:
            raise IntegrityError("a literal is both rated and not rated")
    return Vocabulary(values=dict(values), not_rated=frozenset(not_rated))


def _parse_d1(data: bytes) -> D1Policy:
    obj = _parse_json(data, "D1 policy")
    if not isinstance(obj, dict) or set(obj) != {"cohort", "min_G", "max_largest_share", "min_G_eff"}:
        raise IntegrityError("D1 policy must hold exactly cohort, min_G, max_largest_share, min_G_eff")
    if obj["cohort"] not in WINDOWS:
        raise IntegrityError("D1 policy cohort must be development or holdout")
    if isinstance(obj["min_G"], bool) or not isinstance(obj["min_G"], int) or obj["min_G"] < 0:
        raise IntegrityError("D1 policy min_G must be a non-negative integer")
    share = obj["max_largest_share"]
    if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 <= share <= 1:
        raise IntegrityError("D1 policy max_largest_share must be in [0, 1]")
    eff = obj["min_G_eff"]
    if isinstance(eff, bool) or not isinstance(eff, (int, float)) or eff < 0:
        raise IntegrityError("D1 policy min_G_eff must be non-negative")
    return D1Policy(cohort=obj["cohort"], min_g=obj["min_G"], max_largest_share=float(share), min_g_eff=float(eff))


def _split_ids(data: bytes, label: str) -> list:
    if data == b"":
        return []
    text = data.decode("utf-8")
    if not text.endswith("\n"):
        raise IntegrityError(f"{label} does not end with a newline")
    ids = text[:-1].split("\n")
    seen = set()
    for pid in ids:
        if not PID_RE.fullmatch(pid):
            raise IntegrityError(f"{label} holds a line that is not a project_id")
        if pid in seen:
            raise IntegrityError(f"{label} holds a project_id twice")
        seen.add(pid)
    return ids


def _candidate_rows(data: bytes) -> list:
    reader = csv.DictReader(io.StringIO(data.decode("utf-8"), newline=""))
    if reader.fieldnames is None or not set(CANDIDATE_COLUMNS) <= set(reader.fieldnames):
        raise IntegrityError("structural_candidates.csv lacks a required column")
    return list(reader)


@_at_boundary
def validate_bytes(blobs: Mapping[str, bytes], pins: Pins) -> Inputs:
    """Check every input's bytes against the pins and the cross-file links. No paths are used.

    Any hash mismatch or unset pin raises PinRefused. Any broken link or
    inconsistent value raises IntegrityError. Nothing is repaired.
    """
    # Snapshot: manifest, the digest over its page hashes, then each ieg page.
    snap_manifest_bytes = _blob(blobs, K_SNAP_MANIFEST)
    _pin(sha256_bytes(snap_manifest_bytes), pins.snapshot_manifest_sha, "snapshot manifest")
    snap_manifest = _parse_json(snap_manifest_bytes, "snapshot manifest")
    if not isinstance(snap_manifest, dict) or not isinstance(snap_manifest.get("datasets"), dict):
        raise IntegrityError("snapshot manifest has no datasets")
    lines = []
    for ds in sorted(snap_manifest["datasets"]):
        for page in snap_manifest["datasets"][ds].get("pages", []):
            lines.append(f"{page['sha256']}  {_safe_name(page['file'])}")
    digest = sha256_bytes("".join(line + "\n" for line in sorted(lines)).encode("utf-8"))
    if digest != snap_manifest.get("snapshot_digest"):
        raise PinRefused("snapshot: page digest does not match the manifest's snapshot_digest")
    _pin(digest, pins.snapshot_page_digest, "snapshot page digest")

    ieg = _ieg_block(snap_manifest)
    input_sha = {K_SNAP_MANIFEST: sha256_bytes(snap_manifest_bytes)}
    records_by_pid: dict[str, list] = defaultdict(list)
    total_records = 0
    for page in ieg["pages"]:
        name = _safe_name(page.get("file"))
        data = _blob(blobs, f"snapshot/{name}")
        actual = sha256_bytes(data)
        if actual != page.get("sha256"):
            raise PinRefused("snapshot page sha256 does not match the manifest")
        input_sha[f"snapshot/{name}"] = actual
        obj = _parse_json(data, name, object_pairs_hook=_projection_hook)
        if not isinstance(obj, dict) or not isinstance(obj.get("data"), list) or "count" not in obj:
            raise IntegrityError("page lacks the count or data keys")
        if obj["count"] != ieg["api_total"] or len(obj["data"]) != page.get("records"):
            raise IntegrityError(
                f"{name}: count {obj['count']} (api_total {ieg['api_total']}), data {len(obj['data'])}, "
                f"manifest {page.get('records')}")
        for index, item in enumerate(obj["data"]):
            if not isinstance(item, dict):
                raise IntegrityError("page record is not an object")
            total_records += 1
            rec = _valid_record(name, index, item)
            if rec is not None:
                records_by_pid[rec.project_id].append(rec)
    if total_records != ieg["api_total"]:
        raise IntegrityError("snapshot record count differs from api_total")

    # Structural receipt r2: manifest, then its three files.
    struct_manifest_bytes = _blob(blobs, K_STRUCT_MANIFEST)
    _pin(sha256_bytes(struct_manifest_bytes), pins.structural_manifest_sha, "structural receipt manifest")
    struct_manifest = _parse_json(struct_manifest_bytes, "structural manifest")
    outputs = struct_manifest.get("outputs") if isinstance(struct_manifest, dict) else None
    if not isinstance(outputs, dict):
        raise IntegrityError("structural manifest has no outputs")
    input_sha[K_STRUCT_MANIFEST] = sha256_bytes(struct_manifest_bytes)
    struct_data = {}
    for name in STRUCT_FILES:
        data = _blob(blobs, f"structural/{name}")
        actual = sha256_bytes(data)
        if name not in outputs or actual != outputs[name].get("sha256"):
            raise PinRefused("structural receipt file does not match its manifest")
        struct_data[name] = data
        input_sha[f"structural/{name}"] = actual
    ids_dev = _split_ids(struct_data["split_ids_development.txt"], "split_ids_development.txt")
    ids_hold = _split_ids(struct_data["split_ids_holdout.txt"], "split_ids_holdout.txt")
    if set(ids_dev) & set(ids_hold):
        raise IntegrityError("a project appears in both windows")
    split_ids = {"development": set(ids_dev), "holdout": set(ids_hold)}
    rows = _candidate_rows(struct_data["structural_candidates.csv"])
    seen_pids = set()
    candidates = []
    for row in rows:
        pid = row["project_id"]
        if not PID_RE.fullmatch(pid or ""):
            raise IntegrityError("structural_candidates.csv holds a project_id that is not valid")
        if pid in seen_pids:
            raise IntegrityError("candidate appears twice")
        seen_pids.add(pid)
        window = row["window"]
        if window not in WINDOWS:
            raise IntegrityError("candidate has an unknown window")
        fy = parse_eval_fy(row["evaluation_fy"]) if EVAL_FY_RE.fullmatch(row["evaluation_fy"] or "") else None
        if fy is None:
            raise IntegrityError("candidate has no valid evaluation_fy")
        if window == "development" and fy > DEVELOPMENT_MAX_EVAL_FY:
            raise IntegrityError("candidate is development but its evaluation_fy is outside the development range")
        if window == "holdout" and not HOLDOUT_MIN_EVAL_FY <= fy <= HOLDOUT_MAX_EVAL_FY:
            raise IntegrityError("candidate is holdout but its evaluation_fy is outside the holdout range")
        if pid not in split_ids[window]:
            raise IntegrityError("candidate is not listed in its split file")
        wb = (row["wb_code"] or "").strip()
        if not wb:
            raise IntegrityError("candidate has no wb_code")
        recs = records_by_pid.get(pid)
        if not recs:
            raise IntegrityError("candidate is not in the snapshot")
        max_fy = max(r.evaluation_fy for r in recs)
        if max_fy != fy:
            raise IntegrityError("candidate: snapshot maximum evaluation_fy differs from the receipt")
        at_max = tuple(sorted((r for r in recs if r.evaluation_fy == max_fy), key=lambda r: (r.page, r.index)))
        expected = sorted(f"{r.page}#{r.index}" for r in at_max)
        declared = (row["authoritative_records"] or "").split(";")
        if len(set(declared)) != len(declared) or sorted(declared) != expected:
            raise IntegrityError("candidate: authoritative records differ from the snapshot")
        candidates.append(Candidate(project_id=pid, window=window, evaluation_fy=fy, wb_code=wb, records=at_max))
    if seen_pids != split_ids["development"] | split_ids["holdout"]:
        raise IntegrityError("the split files and structural_candidates.csv name different projects")

    vocab_bytes = _blob(blobs, K_VOCAB)
    _pin(sha256_bytes(vocab_bytes), pins.vocabulary_sha, "vocabulary")
    input_sha[K_VOCAB] = sha256_bytes(vocab_bytes)
    d1_bytes = _blob(blobs, K_D1)
    _pin(sha256_bytes(d1_bytes), pins.d1_policy_sha, "D1 policy")
    input_sha[K_D1] = sha256_bytes(d1_bytes)

    candidates.sort(key=lambda c: c.project_id)
    return Inputs(
        pins=pins,
        input_sha256=dict(sorted(input_sha.items())),
        candidates=tuple(candidates),
        vocabulary=_parse_vocabulary(vocab_bytes),
        d1=_parse_d1(d1_bytes),
    )


# ---------------------------------------------------------------- sealing (13.2, 13.9)

SEAL_MAGIC = b"SORA-R1-SEAL"
SEAL_VERSION = 1
PURPOSE_HOLDOUT = 1
PURPOSE_BLOCKED = 2
SEAL_HEADER_BYTES = len(SEAL_MAGIC) + 2 + 32 + 12
SEAL_INFO = b"SORA-R1-SEAL v1"
INNER_NAME = "inner_sha256.txt"
SEALED_HOLDOUT = "sealed/holdout.r1.enc"
SEALED_BLOCKED = "sealed/blocked_diagnostics.enc"
FIELD_LIMIT = "field_limit_exceeded"
TAR_RECORDSIZE = 10240


@dataclass(frozen=True)
class SealParams:
    """Everything the seal needs: the owner's public key (from R0), the permit bytes (salt) and the
    plaintext capacity (permit.sealed_plaintext_bytes). The private key is never here."""
    recipient_public: bytes
    permit_bytes: bytes
    capacity: int


def inner_sha256(files: Mapping[str, bytes]) -> str:
    """13.10.4: sha256 over name || 0x00 || 8-byte big-endian length || data, names ascending, inner file excluded."""
    h = hashlib.sha256()
    for name in sorted(n for n in files if n != INNER_NAME):
        data = files[name]
        h.update(name.encode("utf-8") + b"\x00" + len(data).to_bytes(8, "big") + data)
    return h.hexdigest()


def _tar_size_bound(data_sizes) -> int:
    """Upper bound of a USTAR archive: 512-byte header and padded data per file, two end blocks, record padding."""
    total = sum(512 + ((d + 511) // 512) * 512 for d in data_sizes) + 1024
    return ((total + TAR_RECORDSIZE - 1) // TAR_RECORDSIZE) * TAR_RECORDSIZE


def sealed_upper_bound(counts: Mapping[str, int]) -> int:
    """Bound on the plaintext of either package. counts: holdout candidates H, records per project M (13.12.3)."""
    holdout_h, max_m = counts["holdout"], counts["max_records"]
    holdout_files = [
        INNER_BYTES,
        (holdout_h + 1) * ROW_MAX,              # target_rows_holdout.csv
        (holdout_h + 1) * ROW_MAX,              # target_exclusions_holdout.csv
        (holdout_h * max_m + 1) * ROW_MAX,      # target_audit_holdout.csv
        8 * holdout_h,                          # final_ids_holdout.txt
        JSON_MAX, JSON_MAX, JSON_MAX,           # country_weights_holdout, d1_holdout_full, qa_holdout
    ]
    blocked_files = [INNER_BYTES, JSON_MAX]     # blocked_detail.json
    return max(_tar_size_bound(holdout_files), _tar_size_bound(blocked_files))


def candidate_counts(candidates_csv: bytes) -> dict:
    """H, the holdout candidates; M, the most records any candidate has at its maximum FY. Read from r2 only."""
    rows = _candidate_rows(candidates_csv)
    return {
        "holdout": sum(1 for r in rows if r["window"] == "holdout"),
        "max_records": max((len((r["authoritative_records"] or "").split(";")) for r in rows), default=0),
    }


def _tar_bytes(files: Mapping[str, bytes]) -> bytes:
    """Deterministic USTAR archive: inner_sha256.txt first, the rest by name (14.13)."""
    order = [INNER_NAME] + sorted(n for n in files if n != INNER_NAME)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name in order:
            if not (name.isascii() and 0 < len(name) <= 100 and "/" not in name):
                raise IntegrityError(FIELD_LIMIT)
            data = files[name]
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o644
            info.type = tarfile.REGTYPE
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _seal_key(shared: bytes, purpose: int, recipient_public: bytes, permit_bytes: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=hashlib.sha256(permit_bytes).digest(),
        info=SEAL_INFO + bytes([purpose]) + recipient_public,
    ).derive(shared)


def _require_seal(inputs: Inputs) -> SealParams:
    if inputs.seal is None:
        raise PinRefused("no sealing parameters: the recipient key and the permit are not set")
    return inputs.seal


def seal_package(files: Mapping[str, bytes], purpose: int, seal: SealParams) -> bytes:
    """Pack the files into a USTAR archive padded to seal.capacity, then encrypt it (X25519 + HKDF + AES-GCM).

    The plaintext exists only in memory. The header is the AAD; ephemeral key and nonce are fresh."""
    files = dict(files)
    files[INNER_NAME] = (inner_sha256(files) + "\n").encode("ascii")
    tar = _tar_bytes(files)
    if len(tar) > seal.capacity:
        raise IntegrityError(FIELD_LIMIT)
    plain = tar + b"\0" * (seal.capacity - len(tar))
    eph = X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes(encoding=serialization.Encoding.Raw,
                                            format=serialization.PublicFormat.Raw)
    nonce = os.urandom(12)
    header = SEAL_MAGIC + bytes([SEAL_VERSION, purpose]) + eph_pub + nonce
    key = _seal_key(eph.exchange(X25519PublicKey.from_public_bytes(seal.recipient_public)),
                    purpose, seal.recipient_public, seal.permit_bytes)
    return header + AESGCM(key).encrypt(nonce, plain, header)


def open_seal(blob: bytes, purpose: int, private_key: X25519PrivateKey, permit_bytes: bytes) -> bytes:
    """Owner-side decryption: returns the padded plaintext. Any header, tag or key mismatch refuses."""
    if len(blob) < SEAL_HEADER_BYTES + 16 or blob[:len(SEAL_MAGIC)] != SEAL_MAGIC:
        raise IntegrityError("seal does not open")
    version, got_purpose = blob[len(SEAL_MAGIC)], blob[len(SEAL_MAGIC) + 1]
    if version != SEAL_VERSION or got_purpose != purpose:
        raise IntegrityError("seal does not open")
    header = blob[:SEAL_HEADER_BYTES]
    eph_pub = blob[SEAL_HEADER_BYTES - 44:SEAL_HEADER_BYTES - 12]
    nonce = blob[SEAL_HEADER_BYTES - 12:SEAL_HEADER_BYTES]
    recipient_public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    try:
        shared = private_key.exchange(X25519PublicKey.from_public_bytes(eph_pub))
        key = _seal_key(shared, purpose, recipient_public, permit_bytes)
        return AESGCM(key).decrypt(nonce, blob[SEAL_HEADER_BYTES:], header)
    except (ValueError, InvalidTag):
        raise IntegrityError("seal does not open") from None


def read_package(plain: bytes) -> dict:
    """Parse a decrypted package and verify its inner hash (13.10.4)."""
    files = {}
    with tarfile.open(fileobj=io.BytesIO(plain), mode="r:") as tar:
        for member in tar.getmembers():
            files[member.name] = tar.extractfile(member).read()
    if files.get(INNER_NAME) != (inner_sha256(files) + "\n").encode("ascii"):
        raise IntegrityError("inner hash does not match the package")
    return files


# ---------------------------------------------------------------- build

def _cohort_stats(countries: list) -> dict:
    n = len(countries)
    tally = Counter(countries)
    if n == 0:
        return {"n_projects": 0, "G": 0, "largest_country_share": None, "G_eff": None, "countries": []}
    shares = [count / n for count in tally.values()]
    return {
        "n_projects": n,
        "G": len(tally),
        "largest_country_share": max(tally.values()) / n,
        "G_eff": 1.0 / sum(s * s for s in shares),
        "countries": [[code, tally[code]] for code in sorted(tally)],
    }


def _policy_passes(stats: dict, policy: D1Policy) -> bool:
    if stats["n_projects"] == 0:
        return False
    return (stats["G"] >= policy.min_g
            and stats["largest_country_share"] <= policy.max_largest_share
            and stats["G_eff"] >= policy.min_g_eff)


def _pins_dict(pins: Pins) -> dict:
    return {
        "snapshot_manifest_sha256": pins.snapshot_manifest_sha,
        "snapshot_page_digest": pins.snapshot_page_digest,
        "structural_receipt_manifest_sha256": pins.structural_manifest_sha,
        "vocabulary_sha256": pins.vocabulary_sha,
        "d1_policy_sha256": pins.d1_policy_sha,
    }


def _public_stats(stats: dict) -> dict:
    return {k: stats[k] for k in ("n_projects", "G", "largest_country_share", "G_eff")}


def _failed_thresholds(stats: dict, policy: D1Policy) -> list:
    failed = []
    if stats["n_projects"] == 0:
        return ["empty_cohort"]
    if stats["G"] < policy.min_g:
        failed.append("min_G")
    if stats["largest_country_share"] > policy.max_largest_share:
        failed.append("max_largest_country_share")
    if stats["G_eff"] < policy.min_g_eff:
        failed.append("min_G_eff")
    return failed


# Window -> names of the analytic files. Only development is written in the open folder (13.3).
WINDOW_FILES = {
    "development": {
        "rows": "target_rows.csv", "excl": "target_exclusions.csv", "audit": "target_audit.csv",
        "ids": "final_ids_development.txt", "weights": "country_weights.json", "qa": "target_qa.json",
    },
    "holdout": {
        "rows": "target_rows_holdout.csv", "excl": "target_exclusions_holdout.csv",
        "audit": "target_audit_holdout.csv", "ids": "final_ids_holdout.txt",
        "weights": "country_weights_holdout.json", "qa": "qa_holdout.json",
    },
}


def _window_accumulator() -> dict:
    return {"rows": [], "excl": [], "audit": [], "ids": [], "countries": [],
            "classes": {"1": 0, "0": 0}, "excluded": {"T1": 0, "T2": 0, "T3": 0, "T4": 0},
            "records": 0, "candidates": 0}


def build(inputs: Inputs) -> Result:
    """Pure: decides every candidate and returns the outputs. Reads no file.

    Open files hold development only, plus holdout_d1 in d1_verdict.json (13.1, 13.3).
    The holdout package is returned in `sealed` as plaintext; it is sealed in a later step.
    """
    vocab = inputs.vocabulary
    unknown_count = 0
    unknown_seen: Counter = Counter()
    for cand in inputs.candidates:
        for rec in cand.records:
            if classify(rec.canon, vocab)[0] == "unknown":
                unknown_count += 1
                unknown_seen[_typed_json(rec.canon)] += 1

    decisions = {}
    for cand in inputs.candidates:
        distinct = sorted({rec.canon for rec in cand.records}, key=_typed_json)
        if len(distinct) > 1:
            decisions[cand.project_id] = ("T4", None, distinct)
            continue
        kind, value = classify(distinct[0], vocab)
        rule = {"absent": "T1", "not_rated": "T2", "unknown": "T3"}.get(kind)
        decisions[cand.project_id] = (rule, None, distinct) if rule else ("kept", value, distinct)

    run_meta = dict(inputs.run_meta)
    if unknown_count > 0:
        # 13.4: counts, types, literal hashes and frequencies go only into the sealed diagnostics,
        # summed over both windows (13.12.3).
        rule_counts = Counter(decisions[c.project_id][0] for c in inputs.candidates)
        detail = {
            "statement": STATEMENT,
            "rule_version": RULE_VERSION,
            "projects_by_rule": {code: rule_counts.get(code, 0) for code in ("T1", "T2", "T3", "T4", "kept")},
            "unknown_count": unknown_count,
            # The typed canonical values themselves, so the owner can add each one to not_rated (13.10.1).
            "unknown_values": [{"canonical": json.loads(text), "count": n}
                               for text, n in sorted(unknown_seen.items())],
        }
        blob = seal_package({"blocked_detail.json": _json_bytes(detail)}, PURPOSE_BLOCKED, _require_seal(inputs))
        receipt = {
            "status": "blocked",
            "reason": "unknown_outcome_values",
            "statement": STATEMENT,
            "rule_version": RULE_VERSION,
            "pins": _pins_dict(inputs.pins),
            "inputs_sha256": dict(inputs.input_sha256),
        }
        return Result("blocked", {"blocked_receipt.json": _json_bytes(receipt)},
                      {SEALED_BLOCKED: blob}, inputs.input_sha256, run_meta)

    acc = {w: _window_accumulator() for w in WINDOWS}
    for cand in sorted(inputs.candidates, key=lambda c: c.project_id):
        rule, value, distinct = decisions[cand.project_id]
        a = acc[cand.window]
        a["candidates"] += 1
        a["records"] += len(cand.records)
        kept = rule == "kept"
        if kept:
            a["rows"].append({
                "project_id": cand.project_id,
                "split": cand.window,
                "ieg_outcome_satisfactory": value,
                "outcome_raw_canonical": distinct[0][1],
                "n_source_records": len(cand.records),
            })
            a["ids"].append(cand.project_id)
            a["countries"].append(cand.wb_code)
            a["classes"][str(value)] += 1
        else:
            a["excluded"][rule] += 1
            a["excl"].append({
                "project_id": cand.project_id,
                "split": cand.window,
                "rule": rule,
                "reason": DISPOSITION_REASON[rule],
                "literals": _typed_json_list(distinct) if rule in ("T3", "T4") else "",
            })
        for rec in cand.records:
            a["audit"].append({
                "project_id": cand.project_id,
                "ieg_page_file": rec.page,
                "ieg_index": rec.index,
                "outcome_present": 1 if rec.outcome_present else 0,
                "outcome_original_json": rec.outcome_json,
                "outcome_canonical": _typed_json(rec.canon),
                "used": 1 if kept else 0,
            })

    thresholds = {
        "min_G": inputs.d1.min_g,
        "max_largest_country_share": inputs.d1.max_largest_share,
        "min_G_eff": inputs.d1.min_g_eff,
    }
    stats = {w: _cohort_stats(acc[w]["countries"]) for w in WINDOWS}
    files: dict[str, bytes] = {}
    sealed: dict[str, bytes] = {}
    for w in WINDOWS:
        a = acc[w]
        names = WINDOW_FILES[w]
        a["audit"].sort(key=lambda r: (r["project_id"], r["ieg_page_file"], r["ieg_index"]))
        tables = {
            names["rows"]: _csv_bytes(
                ("project_id", "split", "ieg_outcome_satisfactory", "outcome_raw_canonical", "n_source_records"),
                sorted(a["rows"], key=lambda r: r["project_id"])),
            names["excl"]: _csv_bytes(
                ("project_id", "split", "rule", "reason", "literals"),
                sorted(a["excl"], key=lambda r: r["project_id"])),
            names["audit"]: _csv_bytes(
                ("project_id", "ieg_page_file", "ieg_index", "outcome_present", "outcome_original_json",
                 "outcome_canonical", "used"),
                a["audit"]),
            names["ids"]: "".join(f"{pid}\n" for pid in sorted(a["ids"])).encode("utf-8"),
            names["weights"]: _json_bytes({"statement": "structural country weights of the cohort; carries no outcome field",
                                           "cohort": stats[w]}),
        }
        qa_block = {
            "kept": len(a["ids"]), "excluded_by_rule": a["excluded"], "class_counts": a["classes"],
            "records_at_max_evaluation_fy": a["records"], "unknown_count": 0}
        if w == "development":
            files.update(tables)
            files["target_qa.json"] = _json_bytes({
                "statement": STATEMENT, "rule_version": RULE_VERSION, "status": "ok",
                "candidates": {"development": a["candidates"], "holdout": acc["holdout"]["candidates"],
                               "total": len(inputs.candidates)},
                "development": qa_block})
        else:
            sealed.update(tables)
            sealed[names["qa"]] = _json_bytes(dict(qa_block, statement=STATEMENT, rule_version=RULE_VERSION))

    dev_pass = _policy_passes(stats["development"], inputs.d1)
    hold_pass = _policy_passes(stats["holdout"], inputs.d1)
    files["d1_verdict.json"] = _json_bytes({
        "statement": D1_STATEMENT,
        "policy_cohort": inputs.d1.cohort,
        "thresholds": thresholds,
        "development": dict(_public_stats(stats["development"]), passes=dev_pass),
        "holdout_d1": "pass" if hold_pass else "fail",
    })
    sealed["d1_holdout_full.json"] = _json_bytes({
        "statement": D1_STATEMENT,
        "thresholds": thresholds,
        "cohort": dict(stats["holdout"], passes=hold_pass,
                       failed_thresholds=_failed_thresholds(stats["holdout"], inputs.d1)),
    })
    blob = seal_package(sealed, PURPOSE_HOLDOUT, _require_seal(inputs))
    return Result("ok", files, {SEALED_HOLDOUT: blob}, inputs.input_sha256, run_meta)


def _typed_json_list(distinct) -> str:
    return json.dumps([list(c) for c in distinct], ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------- output

CORE_DUMPS_DISABLED: bool | None = None


def disable_core_dumps() -> bool:
    """13.2: real runs set the core size limit to zero; the outcome is recorded in the run metadata."""
    global CORE_DUMPS_DISABLED
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        CORE_DUMPS_DISABLED = True
    except (ValueError, OSError):
        CORE_DUMPS_DISABLED = False
    return CORE_DUMPS_DISABLED


def make_run_meta(mode: str, permit_sha256: str | None = None, git: bool = True) -> dict:
    meta = {
        "mode": mode,
        "core_dumps_disabled": CORE_DUMPS_DISABLED if mode == "real" else None,
        "builder_path": "scripts/ieg_target_r1.py",
        "builder_sha256": sha256_bytes(BUILDER_PATH.read_bytes()),
        "permit_sha256": permit_sha256,
        "python": sys.version.split()[0],
        "utc_time": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_sha": None,
        "git_dirty": None,
    }
    if git:
        repo = BUILDER_PATH.parents[1]
        try:
            meta["git_sha"] = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                             capture_output=True, text=True, check=True).stdout.strip()
            meta["git_dirty"] = bool(subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                                                    capture_output=True, text=True, check=True).stdout.strip())
        except (OSError, subprocess.CalledProcessError):
            pass
    return meta


def output_paths(out) -> tuple[Path, Path, Path]:
    out = Path(out)
    return out, out.with_name(out.name + ".partial"), out.with_name(out.name + ".blocked")


def check_output_free(out) -> None:
    for path in output_paths(out):
        if path.exists() or path.is_symlink():
            raise OutputRefused("refusing to write: the output folder already exists")


def write_outputs(result: Result, out) -> Path:
    """Write into <out>.partial, then rename. Blocked results go to <out>.blocked.

    Plaintext is never passed here: the sealed entries are ciphertext (13.2)."""
    final_plain, partial, blocked = output_paths(out)
    check_output_free(out)
    if result.status == "ok":
        final = final_plain
    elif result.status == "blocked":
        final = blocked
    else:
        raise IntegrityError(f"unknown result status {result.status!r}")
    files = dict(result.files)
    sealed = dict(result.sealed)
    everything = {**files, **sealed}
    manifest = {
        "statement": STATEMENT,
        "rule_version": RULE_VERSION,
        "run": dict(result.run_meta),
        "inputs": dict(result.input_sha256),
        "outputs": {name: {"bytes": len(data), "sha256": sha256_bytes(data)}
                    for name, data in sorted(everything.items())},
    }
    files["manifest.json"] = _json_bytes(manifest)
    os.makedirs(partial)
    try:
        for name, data in sorted({**files, **sealed}.items()):
            target = partial / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        os.rename(partial, final)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    return final


# ---------------------------------------------------------------- real mode only (13.5, 13.9-13.11)

R0_OWNER_DECISION = "подписываю R0"
APPROVAL_OWNER_DECISION = "утверждаю словарь"
R1A_OWNER_DECISION = "разрешаю R1a"
R1A_RESULT_KINDS = ("literals_only", "literals_with_counts")
ALLOWED_PUBLIC_OUTCOMES = {"status": ["blocked", "ok"], "holdout_d1": ["fail", "pass"]}
TRUST_KEYS = ("r1_permit_sha256", "r0_receipt_path", "vocabulary_approval_path",
              "r1a_receipt_path", "r1a_procedure_path", "r1a_result_path")
PERMIT_FIELDS = (
    "stage", "r0_receipt_sha256", "vocabulary_approval_sha256", "vocabulary_sha256",
    "r1a_receipt_sha256", "d1_policy_sha256", "builder_sha256", "structural_receipt_manifest_sha256",
    "sealed_plaintext_bytes", "allowed_public_outcomes", "owner_decision", "date_utc",
)
PERMIT_HASH_FIELDS = (
    "r0_receipt_sha256", "vocabulary_approval_sha256", "vocabulary_sha256", "r1a_receipt_sha256",
    "d1_policy_sha256", "builder_sha256", "structural_receipt_manifest_sha256",
)
R0_FIELDS = (
    "stage", "snapshot_manifest_sha256", "snapshot_page_digest", "structural_receipt_manifest_sha256",
    "d1_policy_sha256", "owner_x25519_public_key_hex", "r1a_procedure_sha256", "owner_decision", "date_utc",
)
APPROVAL_FIELDS = ("r0_receipt_sha256", "r1a_receipt_sha256", "vocabulary_sha256", "owner_decision", "date_utc")
R1A_FIELDS = (
    "stage", "r0_receipt_sha256", "r1a_procedure_sha256", "snapshot_manifest_sha256", "snapshot_page_digest",
    "r1a_script_sha256", "result_sha256", "result_kind", "owner_decision", "date_utc",
)
PROCEDURE_FIELDS = ("allowed_r1a_script_sha256", "allowed_result_kind")
_PERMIT_NON_STRING = ("sealed_plaintext_bytes", "allowed_public_outcomes")


def _document(data: bytes, fields, label: str) -> dict:
    """A JSON object with exactly the given keys. Every key is a string except the non-string permit fields."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise PermitRefused(f"{label} is not valid JSON") from None
    if not isinstance(obj, dict):
        raise PermitRefused(f"{label} is not a JSON object")
    missing = [f for f in fields if f not in obj]
    extra = sorted(set(obj) - set(fields))
    if missing or extra:
        raise PermitRefused(f"{label} fields: missing {missing}, unexpected {extra}")
    for f in fields:
        if f not in _PERMIT_NON_STRING and not isinstance(obj[f], str):
            raise PermitRefused(f"{label} field {f} is not a string")
    return obj


def _hashes(obj: dict, names, label: str) -> None:
    for name in names:
        if not isinstance(obj[name], str) or not HEX64_RE.fullmatch(obj[name]):
            raise PermitRefused(f"{label} field {name} is not a sha256")


def _utc(obj: dict, label: str) -> None:
    if not isinstance(obj["date_utc"], str) or not ISO_UTC_RE.fullmatch(obj["date_utc"]):
        raise PermitRefused(f"{label} date_utc is not an ISO-8601 UTC time")


def _same(actual, expected, label: str) -> None:
    """Every link and pin of the chain: an unset expectation refuses, a mismatch refuses (exit 3)."""
    if expected is None:
        raise PinRefused(f"{label}: no pin is set")
    if actual != expected:
        raise PinRefused(f"{label}: does not match")


def _read(path, label: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except (OSError, TypeError) as exc:
        raise PermitRefused(f"{label} unreadable") from None


def _read_trust(path) -> dict:
    try:
        obj = json.loads(Path(path).read_bytes().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise PermitRefused("trust file unreadable") from None
    if not isinstance(obj, dict) or set(obj) != set(TRUST_KEYS):
        raise PermitRefused("trust file must hold exactly the six chain keys")
    for key in TRUST_KEYS:
        value = obj[key]
        if value is None:
            raise PermitRefused(f"trust file holds null for {key}: no R1 permit is trusted")
        if key == "r1_permit_sha256":
            if not isinstance(value, str) or not HEX64_RE.fullmatch(value):
                raise PermitRefused("trust file r1_permit_sha256 is not a sha256")
        elif not isinstance(value, str) or not value:
            raise PermitRefused(f"trust file {key} is not a path")
    return obj


def _parse_permit(data: bytes) -> dict:
    obj = _document(data, PERMIT_FIELDS, "permit")
    if obj["stage"] != "R1":
        raise PermitRefused("permit stage is not R1")
    if obj["owner_decision"] != OWNER_DECISION:
        raise PermitRefused("permit owner_decision is not the approved phrase")
    _utc(obj, "permit")
    _hashes(obj, PERMIT_HASH_FIELDS, "permit")
    capacity = obj["sealed_plaintext_bytes"]
    if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
        raise PermitRefused("permit sealed_plaintext_bytes is not a positive integer")
    if obj["allowed_public_outcomes"] != ALLOWED_PUBLIC_OUTCOMES:
        raise PermitRefused("permit allowed_public_outcomes is not the approved set")
    return obj


def _read_real_structural(blobs: dict) -> None:
    struct = Path(REAL_STRUCTURAL_DIR)
    blobs[K_STRUCT_MANIFEST] = _read(struct / "manifest.json", "structural manifest")
    for name in STRUCT_FILES:
        blobs[f"structural/{name}"] = _read(struct / name, f"structural {name}")


def _structural_bytes_pinned(blobs: dict, pins: Pins) -> None:
    """The structural receipt's manifest and its three files against their pins, before any count is taken."""
    _pin(sha256_bytes(blobs[K_STRUCT_MANIFEST]), pins.structural_manifest_sha, "structural receipt manifest")
    manifest = _parse_json(blobs[K_STRUCT_MANIFEST], "structural manifest")
    outputs = manifest.get("outputs") if isinstance(manifest, dict) else None
    if not isinstance(outputs, dict):
        raise PinRefused("structural manifest has no outputs")
    for name in STRUCT_FILES:
        if name not in outputs or sha256_bytes(blobs[f"structural/{name}"]) != outputs[name].get("sha256"):
            raise PinRefused("structural receipt file does not match its manifest")


def _read_real_snapshot(blobs: dict) -> None:
    """The only reader of IEG pages. Called after every chain check has passed."""
    snap = Path(REAL_SNAPSHOT_DIR)
    manifest = _read(snap / "manifest.json", "snapshot manifest")
    blobs[K_SNAP_MANIFEST] = manifest
    for name in ieg_page_names(manifest):
        blobs[f"snapshot/{name}"] = _read(snap / name, f"snapshot page {name}")


@_at_boundary
def load_real(permit_path, trust_path=None) -> Inputs:
    """The only reader of real data. Core dumps are disabled first (13.2); every chain check (13.5) runs
    before the first IEG page is read."""
    if not disable_core_dumps():
        raise PermitRefused("core dumps could not be disabled; nothing is read")
    trust = _read_trust(TRUST_PATH if trust_path is None else trust_path)
    permit_bytes = _read(permit_path, "permit")
    _same(sha256_bytes(permit_bytes), trust["r1_permit_sha256"], "permit against the trust file")
    permit = _parse_permit(permit_bytes)
    _same(permit["builder_sha256"], sha256_bytes(BUILDER_PATH.read_bytes()), "permit builder_sha256")

    r0_bytes = _read(trust["r0_receipt_path"], "R0 receipt")
    _same(sha256_bytes(r0_bytes), permit["r0_receipt_sha256"], "R0 receipt bytes")
    r0 = _document(r0_bytes, R0_FIELDS, "R0 receipt")
    if r0["stage"] != "R0" or r0["owner_decision"] != R0_OWNER_DECISION:
        raise PinRefused("R0 receipt stage or owner decision is wrong")
    _utc(r0, "R0")
    _hashes(r0, ("snapshot_manifest_sha256", "snapshot_page_digest", "structural_receipt_manifest_sha256",
                 "d1_policy_sha256", "r1a_procedure_sha256"), "R0")
    _hashes(r0, ("owner_x25519_public_key_hex",), "R0")
    _same(r0["snapshot_manifest_sha256"], REAL_PINS.snapshot_manifest_sha, "R0 snapshot manifest pin")
    _same(r0["snapshot_page_digest"], REAL_PINS.snapshot_page_digest, "R0 snapshot page digest pin")
    _same(r0["structural_receipt_manifest_sha256"], REAL_PINS.structural_manifest_sha, "R0 structural pin")
    _same(r0["structural_receipt_manifest_sha256"], permit["structural_receipt_manifest_sha256"],
          "permit structural pin")
    _same(r0["d1_policy_sha256"], REAL_PINS.d1_policy_sha, "R0 D1 pin")
    _same(r0["d1_policy_sha256"], permit["d1_policy_sha256"], "permit D1 pin")
    _same(r0["owner_x25519_public_key_hex"], REAL_OWNER_PUBLIC_KEY_HEX, "R0 owner public key pin")

    proc_bytes = _read(trust["r1a_procedure_path"], "R1a procedure")
    _same(sha256_bytes(proc_bytes), r0["r1a_procedure_sha256"], "R1a procedure bytes")
    proc = _document(proc_bytes, PROCEDURE_FIELDS, "R1a procedure")
    _hashes(proc, ("allowed_r1a_script_sha256",), "R1a procedure")
    if proc["allowed_result_kind"] not in R1A_RESULT_KINDS:
        raise PinRefused("R1a procedure allows an unknown result kind")

    appr_bytes = _read(trust["vocabulary_approval_path"], "vocabulary approval")
    _same(sha256_bytes(appr_bytes), permit["vocabulary_approval_sha256"], "vocabulary approval bytes")
    appr = _document(appr_bytes, APPROVAL_FIELDS, "vocabulary approval")
    if appr["owner_decision"] != APPROVAL_OWNER_DECISION:
        raise PinRefused("vocabulary approval owner decision is wrong")
    _utc(appr, "vocabulary approval")
    _hashes(appr, ("r0_receipt_sha256", "r1a_receipt_sha256", "vocabulary_sha256"), "vocabulary approval")
    _same(appr["r0_receipt_sha256"], sha256_bytes(r0_bytes), "approval link to R0")
    _same(appr["r1a_receipt_sha256"], permit["r1a_receipt_sha256"], "approval link to R1a")
    _same(appr["vocabulary_sha256"], permit["vocabulary_sha256"], "approval vocabulary hash")

    r1a_bytes = _read(trust["r1a_receipt_path"], "R1a receipt")
    _same(sha256_bytes(r1a_bytes), permit["r1a_receipt_sha256"], "R1a receipt bytes")
    r1a = _document(r1a_bytes, R1A_FIELDS, "R1a receipt")
    if r1a["stage"] != "R1a" or r1a["owner_decision"] != R1A_OWNER_DECISION:
        raise PinRefused("R1a receipt stage or owner decision is wrong")
    _utc(r1a, "R1a")
    _hashes(r1a, ("r0_receipt_sha256", "r1a_procedure_sha256", "snapshot_manifest_sha256",
                  "snapshot_page_digest", "r1a_script_sha256", "result_sha256"), "R1a receipt")
    _same(r1a["r0_receipt_sha256"], sha256_bytes(r0_bytes), "R1a link to R0")
    _same(r1a["r1a_procedure_sha256"], r0["r1a_procedure_sha256"], "R1a link to the procedure")
    _same(r1a["snapshot_manifest_sha256"], REAL_PINS.snapshot_manifest_sha, "R1a snapshot manifest pin")
    _same(r1a["snapshot_page_digest"], REAL_PINS.snapshot_page_digest, "R1a snapshot page digest pin")
    _same(r1a["r1a_script_sha256"], proc["allowed_r1a_script_sha256"], "R1a script against the procedure")
    _same(r1a["result_kind"], proc["allowed_result_kind"], "R1a result kind against the procedure")
    result_bytes = _read(trust["r1a_result_path"], "R1a result")
    _same(sha256_bytes(result_bytes), r1a["result_sha256"], "R1a result bytes")

    if None in (REAL_SNAPSHOT_DIR, REAL_STRUCTURAL_DIR, REAL_VOCABULARY_PATH, REAL_D1_PATH):
        raise PermitRefused("a real input path is not configured")
    vocab_bytes = _read(REAL_VOCABULARY_PATH, "vocabulary")
    _same(sha256_bytes(vocab_bytes), REAL_PINS.vocabulary_sha, "vocabulary pin")
    _same(sha256_bytes(vocab_bytes), permit["vocabulary_sha256"], "vocabulary against the permit")
    d1_bytes = _read(REAL_D1_PATH, "D1 policy")
    _same(sha256_bytes(d1_bytes), REAL_PINS.d1_policy_sha, "D1 policy pin")
    _same(sha256_bytes(d1_bytes), permit["d1_policy_sha256"], "D1 policy against the permit")

    blobs = {K_VOCAB: vocab_bytes, K_D1: d1_bytes}
    _read_real_structural(blobs)
    _structural_bytes_pinned(blobs, REAL_PINS)
    needed = sealed_upper_bound(candidate_counts(blobs["structural/structural_candidates.csv"]))
    if permit["sealed_plaintext_bytes"] < needed:
        raise PermitRefused("permit sealed_plaintext_bytes is below the bound")
    _read_real_snapshot(blobs)
    inputs = validate_bytes(blobs, REAL_PINS)
    chain = {
        "chain/r0_receipt": sha256_bytes(r0_bytes),
        "chain/r1a_procedure": sha256_bytes(proc_bytes),
        "chain/vocabulary_approval": sha256_bytes(appr_bytes),
        "chain/r1a_receipt": sha256_bytes(r1a_bytes),
        "chain/r1a_result": sha256_bytes(result_bytes),
    }
    seal = SealParams(recipient_public=bytes.fromhex(r0["owner_x25519_public_key_hex"]),
                      permit_bytes=permit_bytes, capacity=permit["sealed_plaintext_bytes"])
    return dataclasses.replace(
        inputs,
        input_sha256=dict(sorted({**inputs.input_sha256, **chain}.items())),
        run_meta=make_run_meta("real", permit_sha256=sha256_bytes(permit_bytes)),
        seal=seal,
    )


HELP = """usage:
  python scripts/ieg_target_r1.py --help
  python scripts/ieg_target_r1.py --real --r1-permit FILE --out DIR

R1 target-only builder. --real needs a signed R1 permit whose sha256 is in
scripts/ieg_target_r1_trust.json. --out must not exist. Exit codes: 0 ok,
3 refused before any data read, 4 blocked (DIR.blocked), 5 integrity error.
"""


REFUSED_CODE = "refused"
INTEGRITY_CODE = "integrity_error"
BLOCKED_CODE = "blocked"


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["--help"], ["-h"]):
        print(HELP)
        return 0
    if len(args) != 5 or args[0] != "--real" or args[1] != "--r1-permit" or args[3] != "--out":
        print(REFUSED_CODE, file=sys.stderr)
        return 3
    permit, out = args[2], args[4]
    # Errors print a fixed code only: no message, locator or value of any input (13.1, review P2).
    try:
        check_output_free(out)
        inputs = load_real(permit)
        result = build(inputs)
        write_outputs(result, out)
    except (PinRefused, OutputRefused):
        print(REFUSED_CODE, file=sys.stderr)
        return 3
    except Exception:
        print(INTEGRITY_CODE, file=sys.stderr)
        return 5
    if result.status == "blocked":
        print(BLOCKED_CODE, file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
