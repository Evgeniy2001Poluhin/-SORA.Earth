#!/usr/bin/env python3
"""World Bank country registry snapshot for the IEG country crosswalk (step B0).

Fetches the World Bank country API once over HTTPS, keeps the bytes exactly as
received, checks the documented shape, and writes a new directory holding the
raw response and a manifest. The registry is the only pinned reference for
country identity used by the crosswalk; nothing reads it online during a run.

The script reads no outcome, rating, or project field; it has no such input.
It imports nothing from the application. It opens a network connection only in
fetch(), and only when run as a program; the tests replace the transport.

    GET https://api.worldbank.org/v2/country?format=json&per_page=1000

The response is [meta, records]. It is refused unless meta.pages is 1, meta.total
equals the number of records, and every record has a unique id. A record whose
region.value is "Aggregates" is a WB aggregate, not a country; the manifest
counts countries and aggregates separately.

Output: a new directory. Nothing is overwritten and nothing is deleted.
    raw/countries.json    the response bytes, unchanged
    manifest.json         source, status, headers, fetch time, hashes, counts,
                          and the tool's git SHA with its dirty flag

Exit status: 0 published; 1 refused or failed before the rename, so no final
directory exists; 3 published, but the fsync of the parent directory failed,
so durability is not confirmed.

Usage:
    python scripts/snapshot_wb_countries.py --out DIR
"""

import argparse
import hashlib
import http.client
import json
import os
import platform
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "1"
TOOL_PATH = "scripts/snapshot_wb_countries.py"
DATASET = "world_bank_country_registry"

COUNTRY_URL = "https://api.worldbank.org/v2/country?format=json&per_page=1000"
EXPECTED_HOST = "api.worldbank.org"
MAX_BYTES = 8 * 1024 * 1024
TIMEOUT_S = 60.0
ATTEMPTS = 3
RETRY_WAIT_S = (10.0, 30.0)  # the waits after attempts 1 and 2
USER_AGENT = (
    "sora-earth-ieg-research/1 (world bank country registry snapshot; "
    "https://github.com/Evgeniy2001Poluhin/-SORA.Earth)"
)

RAW_DIR = "raw"
RAW_FILE = "raw/countries.json"
OUT_MANIFEST = "manifest.json"
PARTIAL_SUFFIX = ".partial"
AGGREGATE_REGION = "Aggregates"


class ToolError(Exception):
    """A refusal, reported as a message without a traceback."""


class FetchError(ToolError):
    """The resource could not be fetched: an HTTP error, a retry cap, or a host outside the pin."""


class OversizeError(ToolError):
    """The response body is larger than MAX_BYTES."""


class SourceRefused(ToolError):
    """The bytes are not the documented country registry, or fail a count or uniqueness check."""


class OutputRefused(ToolError):
    """The destination cannot be used. Nothing was written."""


class OutputFailed(ToolError):
    """Writing failed before the rename. No final directory exists."""


class DurabilityUnconfirmed(ToolError):
    """The directory was renamed into place, but the parent fsync failed."""


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


# --- transport -----------------------------------------------------------------------


@dataclass
class Response:
    status: int
    headers: dict  # lower-case names
    body: bytes  # empty unless status is 200
    final_url: str
    redirects: list  # [{"status", "from", "to"}] in the order followed


class _RedirectRecorder(urllib.request.HTTPRedirectHandler):
    """Follows redirects as urllib does, and keeps the chain for the manifest."""

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
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
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


def _check_pinned_url(url, what):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname != EXPECTED_HOST:
        raise FetchError(f"{what} {url} is not an https URL on {EXPECTED_HOST}")


def fetch(url, *, transport=None, sleep=None):
    """Return (Response, attempts). At most ATTEMPTS attempts; only 429, 5xx and transport errors retry.

    A 404 or any other non-retryable status fails at once. An oversize body fails at once.
    The transport and the sleep are looked up when the call is made, so a test can replace them.
    """
    transport = _open_over_network if transport is None else transport
    sleep = time.sleep if sleep is None else sleep
    _check_pinned_url(url, "the registry URL")
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
                    _check_pinned_url(hop["to"], "a redirect target")
                _check_pinned_url(resp.final_url, "the final URL")
                return resp, attempts
            if not (resp.status == 429 or 500 <= resp.status <= 599):
                raise FetchError(f"HTTP {resp.status} is not retried")
        if attempt < ATTEMPTS:
            sleep(RETRY_WAIT_S[attempt - 1])
    raise FetchError(f"{ATTEMPTS} attempts failed; the last outcome was {attempts[-1]}")


# --- the documented shape ----------------------------------------------------------------


def _strict_json(body):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise SourceRefused("duplicate key in a JSON object")
            out[key] = value
        return out

    def refuse_constant(name):
        raise SourceRefused("non-finite number is not allowed")

    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise SourceRefused("the response is not UTF-8") from None
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=refuse_constant)
    except json.JSONDecodeError as exc:
        raise SourceRefused(f"the response is not JSON (line {exc.lineno})") from None


def _as_count(value, name):
    """A count is a JSON integer or a string of digits. A bool is never a count."""
    if isinstance(value, bool):
        raise SourceRefused(f"meta.{name} is a boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit() and value.isascii():
        return int(value)
    raise SourceRefused(f"meta.{name} is not a count")


def _nonempty_text(value):
    return isinstance(value, str) and value != ""


def parse_registry(body):
    """Return (meta, records) from the response bytes, or refuse.

    The response must be [meta, records] with meta.pages == 1 and meta.total equal
    to the number of records. Every record needs a non-empty id, iso2Code and name,
    and a region with a value. Ids must be unique. Nothing else in a record is read.
    """
    doc = _strict_json(body)
    if not (isinstance(doc, list) and len(doc) == 2 and isinstance(doc[0], dict) and isinstance(doc[1], list)):
        raise SourceRefused("the response is not the documented [meta, records] shape")
    meta, records = doc
    total = _as_count(meta.get("total"), "total")
    pages = _as_count(meta.get("pages"), "pages")
    if pages != 1:
        raise SourceRefused(f"meta.pages is {pages}; this snapshot reads exactly one page")
    if total != len(records):
        raise SourceRefused(f"meta.total is {total} but the response holds {len(records)} records")
    seen = set()
    checked = []
    for index, rec in enumerate(records):
        if not isinstance(rec, dict):
            raise SourceRefused(f"record {index} is not an object")
        rid, iso2, name, region = rec.get("id"), rec.get("iso2Code"), rec.get("name"), rec.get("region")
        if not (_nonempty_text(rid) and _nonempty_text(iso2) and _nonempty_text(name)):
            raise SourceRefused(f"record {index} lacks a usable id, iso2Code or name")
        if not (isinstance(region, dict) and isinstance(region.get("value"), str)):
            raise SourceRefused(f"record {index} ({rid}) lacks a region value")
        if rid in seen:
            raise SourceRefused(f"id {rid!r} appears more than once")
        seen.add(rid)
        checked.append(rec)
    return meta, checked


def registry_counts(records):
    """Countries and aggregates, counted by region value. An aggregate is not a country."""
    aggregates = sum(1 for rec in records if rec["region"]["value"] == AGGREGATE_REGION)
    return {"record_count": len(records), "country_count": len(records) - aggregates,
            "aggregate_count": aggregates, "distinct_ids": len({rec["id"] for rec in records})}


# --- raw bytes and the manifest -------------------------------------------------------


def _fetch_record(resp, attempts, body, fetched_at):
    return {
        "url": COUNTRY_URL,
        "fetched_at_utc": fetched_at,
        "final_url": resp.final_url,
        "redirects": resp.redirects,
        "http_status": resp.status,
        "content_type": resp.headers.get("content-type"),
        "content_length_header": resp.headers.get("content-length"),
        "etag": resp.headers.get("etag"),
        "last_modified": resp.headers.get("last-modified"),
        "bytes": len(body),
        "sha256": _sha256(body),
        "attempts": attempts,
        "tls_verified": True,
        "timeout_s": TIMEOUT_S,
        "size_cap_bytes": MAX_BYTES,
        "attempt_cap": ATTEMPTS,
        "user_agent": USER_AGENT,
    }


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


def _json_bytes(obj):
    return (json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


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


def _publish(out, files):
    """Write into <out>.partial, verify it reads back, fsync, rename, then fsync the parent.

    Any failure before the rename leaves no final directory. Nothing is deleted: a
    leftover .partial is reported and kept.
    """
    partial = out.with_name(out.name + PARTIAL_SUFFIX)
    try:
        os.mkdir(partial)
        os.mkdir(partial / RAW_DIR)
    except FileExistsError:
        raise OutputRefused(f"{partial} exists; an earlier run did not finish, and it is not removed automatically") from None
    for rel in sorted(files):
        _write_exclusive(partial / rel, files[rel])
    for rel in sorted(files):
        if (partial / rel).read_bytes() != files[rel]:
            raise OutputFailed(f"{rel} does not read back as written")
    _fsync_tree_bottom_up(partial)
    os.rename(partial, out)
    try:
        _fsync_dir(out.parent)
    except OSError as exc:
        raise DurabilityUnconfirmed(
            f"{out} exists; the fsync of its parent failed ({exc.strerror or exc}), so durability is not confirmed"
        ) from exc


def build(out, *, transport=None, sleep=None, url=COUNTRY_URL):
    """Fetch the registry, check it, and publish. Return the sha256 of manifest.json."""
    out = Path(out)
    _check_destination(out)
    fetched_at = _utc_now()
    resp, attempts = fetch(url, transport=transport, sleep=sleep)
    if "json" not in resp.headers.get("content-type", "").lower():
        raise SourceRefused(f"the content type {resp.headers.get('content-type')!r} is not JSON")
    body = resp.body
    meta, records = parse_registry(body)
    counts = registry_counts(records)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset": DATASET,
        "tool": _tool_identity(),
        "source": _fetch_record(resp, attempts, body, fetched_at),
        "raw": {"file": RAW_FILE, "sha256": _sha256(body), "bytes": len(body)},
        "validated": {
            "meta": {key: meta.get(key) for key in ("page", "pages", "per_page", "total", "lastupdated")},
            **counts,
        },
        "outputs": {RAW_FILE: {"bytes": len(body), "sha256": _sha256(body)}},
    }
    manifest_bytes = _json_bytes(manifest)
    _publish(out, {RAW_FILE: body, OUT_MANIFEST: manifest_bytes})
    return _sha256(manifest_bytes)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Snapshot the World Bank country registry, as received.")
    parser.add_argument("--out", required=True, type=Path, help="new output directory; must not exist")
    args = parser.parse_args(argv)
    try:
        sha = build(args.out)
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
