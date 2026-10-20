#!/usr/bin/env python3
"""R1a literals-only script for the SORA.Earth IEG pilot.

Specification: R1A-LITERALS-SPEC-2026-10-20, sections 1-7, as amended by sections 8 and 9
(later sections prevail). It writes one sorted list of the distinct typed canonical values
of the `outcome` field over every record of the pinned IEG snapshot. No frequency, project
id, window, fiscal year or country is written.

Only this command line runs on real data:

    python scripts/ieg_r1a_literals.py --help
    python scripts/ieg_r1a_literals.py --real --r1a-permit FILE --out DIR

Any other command line exits 3 before any data file is opened. A real run also exits 3
while scripts/ieg_r1a_literals_trust.json holds null.

Exit codes: 0 ok; 3 refused before any data read; 5 integrity error, or the result is over
RESULT_LIMIT. stdout and stderr carry only the fixed codes below, and --help prints its text.
"""

from __future__ import annotations

import datetime as _dt
import functools
import hashlib
import json
import os
import re
import resource
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

RESULT_KIND = "literals_only"
RESULT_FILE = "r1a_literals.json"
RECEIPT_FILE = "r1a_receipt.json"
RESULT_LIMIT = 1048576                 # spec 3: 1 MiB, checked on the serialized file
RESULT_LIMIT_CODE = "result_limit_exceeded"
INTEGRITY_CODE = "integrity_error"
CLEANUP_CODE = "cleanup_failed"
REFUSED_CODE = "refused"
OK_CODE = "ok"
OUTCOME = "outcome"
PAGE_KEYS = ("count", "data")
OWNER_DECISION = "разрешаю R1a"
R0_OWNER_DECISION = "подписываю R0"
PERMIT_STAGE = "R1a-permit"

# Section 4 and 8.1 schemas, as amended by 9.1 (the procedure has exactly two fields).
PERMIT_FIELDS = ("stage", "r0_receipt_sha256", "r1a_procedure_sha256", "script_sha256", "owner_decision", "date_utc")
PERMIT_HEX_FIELDS = ("r0_receipt_sha256", "r1a_procedure_sha256", "script_sha256")
R0_FIELDS = (
    "stage", "snapshot_manifest_sha256", "snapshot_page_digest", "structural_receipt_manifest_sha256",
    "d1_policy_sha256", "owner_x25519_public_key_hex", "r1a_procedure_sha256", "owner_decision", "date_utc",
)
R0_HEX_FIELDS = (
    "snapshot_manifest_sha256", "snapshot_page_digest", "structural_receipt_manifest_sha256",
    "d1_policy_sha256", "owner_x25519_public_key_hex", "r1a_procedure_sha256",
)
PROCEDURE_FIELDS = ("allowed_r1a_script_sha256", "allowed_result_kind")
TRUST_KEYS = ("r1a_permit_sha256", "r0_receipt_path", "r1a_procedure_path")

HEX64_RE = re.compile(r"[0-9a-f]{64}")
ISO_UTC_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
K_SNAP_MANIFEST = "manifest.json"


class PinRefused(RuntimeError):
    """An input does not match its pin, or a pin is not set. Refused before any data read."""


class PermitRefused(PinRefused):
    """The trust file, the permit, R0 or the procedure is malformed or does not allow the run."""


class OutputRefused(RuntimeError):
    """The output folder, or its .partial folder, already exists."""


class IntegrityError(RuntimeError):
    """An input is malformed or inconsistent, or the result is over the limit."""


def _at_boundary(fn):
    """Any exception other than the four classes above leaves as the fixed integrity code (spec 6)."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (PinRefused, OutputRefused, IntegrityError):
            raise
        except Exception:
            raise IntegrityError(INTEGRITY_CODE) from None
    return wrapper


@dataclass(frozen=True)
class Pins:
    snapshot_manifest_sha: str
    snapshot_page_digest: str
    structural_manifest_sha: str


# Real-mode constants: the full snapshot pins of spec 8.2, and the structural receipt pin that R0 must carry.
REAL_PINS = Pins(
    snapshot_manifest_sha="b3d43f8033367d79bb14ace692f8cada7068e98a3540fdfbbc0a1bea2505474c",
    snapshot_page_digest="960d776e8db25a59abcb3562e921c1643ace32466d71682c017b2912dc43d4f3",
    structural_manifest_sha="36b541dbf74f87762a292125ac454c981ac7381ddce05faae4b97514a2c53ef4",
)
REAL_SNAPSHOT_DIR = "/Users/evgenijpoluhin/sora-research/worldbank-snapshots/2026-10-15-sorted"
BUILDER_PATH = Path(__file__).resolve()
TRUST_PATH = BUILDER_PATH.with_name("ieg_r1a_literals_trust.json")


# ---------------------------------------------------------------- helpers

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _same(actual, expected, label: str) -> None:
    """Every link and pin of the chain: an unset expectation refuses, a mismatch refuses."""
    if expected is None:
        raise PinRefused(f"{label}: no pin is set")
    if actual != expected:
        raise PinRefused(f"{label}: does not match")


def _safe_name(name) -> str:
    """A relative path inside the snapshot folder: no absolute path, no '..' and no empty part (as R1 _safe_name)."""
    if not isinstance(name, str) or not name or name.startswith("/") or "\\" in name:
        raise IntegrityError("manifest names a file outside its folder")
    if any(part in ("", ".", "..") for part in name.split("/")):
        raise IntegrityError("manifest names a file outside its folder")
    return name


def _parse_json(data: bytes, **kwargs):
    try:
        return json.loads(data.decode("utf-8"), **kwargs)
    except (UnicodeDecodeError, ValueError):
        raise IntegrityError(INTEGRITY_CODE) from None


def _read(path, label: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except (OSError, TypeError):
        raise PermitRefused(f"{label} unreadable") from None


def _document(data: bytes, fields, label: str) -> dict:
    """A JSON object with exactly the given keys, every value a string."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise PermitRefused(f"{label} is not valid JSON") from None
    if not isinstance(obj, dict):
        raise PermitRefused(f"{label} is not a JSON object")
    if any(f not in obj for f in fields):
        raise PermitRefused(f"{label} has a missing field")
    if set(obj) - set(fields):
        raise PermitRefused(f"{label} has an unexpected field")
    if any(not isinstance(obj[f], str) for f in fields):
        raise PermitRefused(f"{label} has a field that is not a string")
    return obj


def _hashes(obj: dict, names, label: str) -> None:
    for name in names:
        if not HEX64_RE.fullmatch(obj[name]):
            raise PermitRefused(f"{label} has a field that is not a sha256")


def _utc(obj: dict, label: str) -> None:
    value = obj["date_utc"]
    try:
        if not ISO_UTC_RE.fullmatch(value):
            raise ValueError(value)
        _dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise PermitRefused(f"{label} date_utc is not an ISO-8601 UTC time") from None


def _read_trust(path) -> dict:
    try:
        obj = json.loads(Path(path).read_bytes().decode("utf-8"))
    except (OSError, TypeError, ValueError):
        raise PermitRefused("trust file unreadable") from None
    if not isinstance(obj, dict) or set(obj) != set(TRUST_KEYS):
        raise PermitRefused("trust file must hold exactly the chain keys")
    for key in TRUST_KEYS:
        value = obj[key]
        if value is None:
            raise PermitRefused("trust file holds null: no R1a permit is trusted")
        if key == "r1a_permit_sha256":
            if not isinstance(value, str) or not HEX64_RE.fullmatch(value):
                raise PermitRefused("trust file r1a_permit_sha256 is not a sha256")
        elif not isinstance(value, str) or not value:
            raise PermitRefused("trust file has a chain entry that is not a path")
    return obj


# ---------------------------------------------------------------- canonicalization (R1 spec 11.3)

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


def _typed_json(canon: tuple) -> str:
    return json.dumps(list(canon), ensure_ascii=False, separators=(",", ":"))


def _projection_hook(pairs):
    """Keeps only `outcome` and the page keys count and data, as each object is built (spec 2)."""
    return {k: v for k, v in pairs if k == OUTCOME or k in PAGE_KEYS}


# ---------------------------------------------------------------- snapshot (spec 8.2, 9.2)

def _ieg_block(manifest: dict) -> dict:
    block = manifest["datasets"].get("ieg")
    if not isinstance(block, dict) or not isinstance(block.get("pages"), list) or not _is_int(block.get("api_total")):
        raise IntegrityError("snapshot manifest has no ieg dataset block")
    for page in block["pages"]:
        if not isinstance(page, dict):
            raise IntegrityError("manifest page entry is not an object")
    return block


@_at_boundary
def check_snapshot_manifest(manifest_bytes: bytes, pins: Pins) -> dict:
    """Manifest pin, page digest over every dataset's pages, digest pin. Returns the ieg block."""
    _same(sha256_bytes(manifest_bytes), pins.snapshot_manifest_sha, "snapshot manifest")
    manifest = _parse_json(manifest_bytes)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("datasets"), dict):
        raise IntegrityError("snapshot manifest has no datasets")
    lines = []
    for name in sorted(manifest["datasets"]):
        block = manifest["datasets"][name]
        pages = block.get("pages", []) if isinstance(block, dict) else None
        if not isinstance(pages, list):
            raise IntegrityError("snapshot manifest dataset has no page list")
        for page in pages:
            if not isinstance(page, dict) or not isinstance(page.get("sha256"), str):
                raise IntegrityError("manifest page entry is malformed")
            lines.append(f"{page['sha256']}  {_safe_name(page.get('file'))}")
    digest = sha256_bytes("".join(line + "\n" for line in sorted(lines)).encode("utf-8"))
    if digest != manifest.get("snapshot_digest"):
        raise PinRefused("snapshot: page digest does not match the manifest's snapshot_digest")
    _same(digest, pins.snapshot_page_digest, "snapshot page digest")
    return _ieg_block(manifest)


def ieg_page_names(ieg: dict) -> list:
    return [_safe_name(page.get("file")) for page in ieg["pages"]]


@_at_boundary
def check_ieg_pages(ieg: dict, pages: Mapping[str, bytes]) -> list:
    """Byte, count and record checks of the ieg pages. Returns each page's projected records, in manifest order."""
    page_items = []
    total = 0
    for page in ieg["pages"]:
        name = _safe_name(page.get("file"))
        if name not in pages:
            raise IntegrityError("snapshot page is missing")
        data = pages[name]
        if sha256_bytes(data) != page.get("sha256"):
            raise PinRefused("snapshot page sha256 does not match the manifest")
        obj = _parse_json(data, object_pairs_hook=_projection_hook)
        if not isinstance(obj, dict) or "count" not in obj or not isinstance(obj.get("data"), list):
            raise IntegrityError("page lacks the count or data keys")
        if not _is_int(obj["count"]) or obj["count"] != ieg["api_total"]:
            raise IntegrityError("page count differs from api_total")
        records = page.get("records")
        if not _is_int(records) or len(obj["data"]) != records:
            raise IntegrityError("page record number differs from the manifest")
        for item in obj["data"]:
            if not isinstance(item, dict):
                raise IntegrityError("page record is not an object")
        page_items.append(obj["data"])
        total += records
    if total != ieg["api_total"]:
        raise IntegrityError("snapshot record count differs from api_total")
    return page_items


def validate_snapshot_bytes(manifest_bytes: bytes, ieg_pages: Mapping[str, bytes], pins: Pins) -> list:
    """The snapshot part of R1 validate_bytes (spec 9.2). Reads no path; only the ieg pages are needed."""
    return check_ieg_pages(check_snapshot_manifest(manifest_bytes, pins), ieg_pages)


# ---------------------------------------------------------------- result (spec 3, 5)

def extract_literals(page_items) -> dict:
    """Canonical value -> number of records holding it, over every record. Absent kinds are left out (spec 3)."""
    counts: dict = {}
    for items in page_items:
        for item in items:
            canon = canonical_outcome(OUTCOME in item, item.get(OUTCOME))
            if canon[0] in ("string", "nonstring"):
                counts[canon] = counts.get(canon, 0) + 1
    return counts


def literals_bytes(counts: Mapping[tuple, int]) -> bytes:
    ordered = sorted(counts, key=lambda canon: _typed_json(canon).encode("utf-8"))
    obj = {"kind": RESULT_KIND, "values": [list(canon) for canon in ordered]}
    data = (json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(data) > RESULT_LIMIT:
        raise IntegrityError(RESULT_LIMIT_CODE)
    return data


def receipt_bytes(chain: Mapping, literals: bytes, pins: Pins) -> bytes:
    """The R1a receipt in the schema R1 checks (R1 spec 13.10.3). It carries no count of any kind."""
    obj = {
        "stage": "R1a",
        "r0_receipt_sha256": chain["r0"],
        "r1a_procedure_sha256": chain["procedure"],
        "snapshot_manifest_sha256": pins.snapshot_manifest_sha,
        "snapshot_page_digest": pins.snapshot_page_digest,
        "r1a_script_sha256": chain["script"],
        "result_sha256": sha256_bytes(literals),
        "result_kind": RESULT_KIND,
        "owner_decision": OWNER_DECISION,
        "date_utc": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _partial_of(final: Path) -> Path:
    return final.with_name(final.name + ".partial")


def check_output_free(out) -> None:
    """Spec 6 and 8.3: the output's parent folder must exist, and neither <out> nor <out>.partial may exist.
    Checked before any data read, so a missing parent is refused (exit 3) rather than created."""
    final = Path(out)
    if not final.parent.is_dir():
        raise OutputRefused("the parent of the output folder does not exist")
    for path in (final, _partial_of(final)):
        if path.exists() or path.is_symlink():
            raise OutputRefused("the output folder or its partial folder exists")


def _remove_partial(partial: Path) -> None:
    """Remove <out>.partial after a failure. No error is swallowed: a folder that is still there, or a removal
    that raises, is reported as cleanup_failed (exit 5), with the leftover left on disk for the owner."""
    try:
        shutil.rmtree(partial)
    except FileNotFoundError:
        pass
    except OSError:
        raise IntegrityError(CLEANUP_CODE) from None
    if partial.exists() or partial.is_symlink():
        raise IntegrityError(CLEANUP_CODE)


def write_result(out, files: Mapping[str, bytes]) -> Path:
    """Write into <out>.partial, then rename it to <out>. Any failure after the folder exists removes it (spec 8.3)."""
    final = Path(out)
    partial = _partial_of(final)
    check_output_free(final)
    partial.mkdir()
    done = False
    try:
        for name in sorted(files):
            with open(partial / name, "wb") as handle:
                handle.write(files[name])
        os.rename(partial, final)
        done = True
    finally:
        if not done:
            _remove_partial(partial)
    return final


# ---------------------------------------------------------------- chain (spec 4, 8.1, 9.1)

CORE_DUMPS_DISABLED: bool | None = None


def disable_core_dumps() -> bool:
    """Spec 6: the core size limit is set to zero before any page is read."""
    global CORE_DUMPS_DISABLED
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        CORE_DUMPS_DISABLED = True
    except (ValueError, OSError):
        CORE_DUMPS_DISABLED = False
    return CORE_DUMPS_DISABLED


def own_script_sha() -> str:
    return sha256_bytes(BUILDER_PATH.read_bytes())


@_at_boundary
def load_chain(permit_path, trust_path=None) -> dict:
    """Every link of the R1a chain, checked before any snapshot file is opened."""
    trust = _read_trust(TRUST_PATH if trust_path is None else trust_path)
    permit_bytes = _read(permit_path, "permit")
    _same(sha256_bytes(permit_bytes), trust["r1a_permit_sha256"], "permit against the trust file")
    permit = _document(permit_bytes, PERMIT_FIELDS, "permit")
    if permit["stage"] != PERMIT_STAGE:
        raise PermitRefused("permit stage is not R1a-permit")
    if permit["owner_decision"] != OWNER_DECISION:
        raise PermitRefused("permit owner_decision is not the approved phrase")
    _utc(permit, "permit")
    _hashes(permit, PERMIT_HEX_FIELDS, "permit")

    r0_bytes = _read(trust["r0_receipt_path"], "R0 receipt")
    _same(sha256_bytes(r0_bytes), permit["r0_receipt_sha256"], "R0 receipt bytes")
    r0 = _document(r0_bytes, R0_FIELDS, "R0 receipt")
    if r0["stage"] != "R0" or r0["owner_decision"] != R0_OWNER_DECISION:
        raise PinRefused("R0 receipt stage or owner decision is wrong")
    _utc(r0, "R0")
    # d1_policy_sha256 and owner_x25519_public_key_hex are format-only here, by spec 8.1: R1a pins only the
    # snapshot and the structural receipt. R1 pins both (ieg_target_r1.py load_real), so a substituted R0
    # is refused there even though this script accepts a well-formed value.
    _hashes(r0, R0_HEX_FIELDS, "R0")
    _same(r0["snapshot_manifest_sha256"], REAL_PINS.snapshot_manifest_sha, "R0 snapshot manifest pin")
    _same(r0["snapshot_page_digest"], REAL_PINS.snapshot_page_digest, "R0 snapshot page digest pin")
    _same(r0["structural_receipt_manifest_sha256"], REAL_PINS.structural_manifest_sha, "R0 structural pin")
    _same(r0["r1a_procedure_sha256"], permit["r1a_procedure_sha256"], "permit procedure against R0")

    proc_bytes = _read(trust["r1a_procedure_path"], "R1a procedure")
    _same(sha256_bytes(proc_bytes), permit["r1a_procedure_sha256"], "R1a procedure bytes")
    _same(sha256_bytes(proc_bytes), r0["r1a_procedure_sha256"], "R1a procedure bytes against R0")
    proc = _document(proc_bytes, PROCEDURE_FIELDS, "R1a procedure")
    _hashes(proc, ("allowed_r1a_script_sha256",), "R1a procedure")
    own = own_script_sha()
    _same(permit["script_sha256"], own, "permit script against this script")
    _same(proc["allowed_r1a_script_sha256"], own, "R1a procedure script against this script")
    if proc["allowed_result_kind"] != RESULT_KIND:
        raise PinRefused("R1a procedure allows another result kind")
    return {"r0": sha256_bytes(r0_bytes), "procedure": sha256_bytes(proc_bytes), "script": own}


@_at_boundary
def run_real(permit_path, out, trust_path=None) -> Path:
    """The only reader of real data. Every chain check runs before the first IEG page is read."""
    check_output_free(out)
    if not disable_core_dumps():
        raise PermitRefused("core dumps could not be disabled; nothing is read")
    chain = load_chain(permit_path, trust_path)
    snap = Path(REAL_SNAPSHOT_DIR)
    manifest_bytes = _read(snap / K_SNAP_MANIFEST, "snapshot manifest")
    ieg = check_snapshot_manifest(manifest_bytes, REAL_PINS)
    pages = {name: _read(snap / name, "snapshot page") for name in ieg_page_names(ieg)}
    literals = literals_bytes(extract_literals(check_ieg_pages(ieg, pages)))
    receipt = receipt_bytes(chain, literals, REAL_PINS)
    return write_result(out, {RESULT_FILE: literals, RECEIPT_FILE: receipt})


# ---------------------------------------------------------------- command line

HELP = """usage:
  python scripts/ieg_r1a_literals.py --help
  python scripts/ieg_r1a_literals.py --real --r1a-permit FILE --out DIR

R1a literals-only script. --real needs an R1a permit whose sha256 is in
scripts/ieg_r1a_literals_trust.json. --out must not exist. Exit codes: 0 ok,
3 refused before any data read, 5 integrity error or result limit exceeded.
"""


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--help"]:
        print(HELP)
        return 0
    if len(args) != 5 or args[0] != "--real" or args[1] != "--r1a-permit" or args[3] != "--out":
        print(REFUSED_CODE, file=sys.stderr)
        return 3
    # Errors print a fixed code only: no message, locator or value of any input.
    try:
        run_real(args[2], args[4])
    except (PinRefused, OutputRefused):
        print(REFUSED_CODE, file=sys.stderr)
        return 3
    except IntegrityError as exc:
        if exc.args == (RESULT_LIMIT_CODE,):
            code = RESULT_LIMIT_CODE
        elif exc.args == (CLEANUP_CODE,):
            code = CLEANUP_CODE
        else:
            code = INTEGRITY_CODE
        print(code, file=sys.stderr)
        return 5
    except Exception:
        print(INTEGRITY_CODE, file=sys.stderr)
        return 5
    print(OK_CODE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
