#!/usr/bin/env python3
"""Snapshot two public World Bank datasets into an immutable raw directory.

This is the first step of a research pilot that will train a model on
independent project outcomes. The outcome labels are the World Bank Independent
Evaluation Group (IEG) project performance ratings; the project attributes are
the Projects & Operations (P&O) records. Both are revised by their publisher,
so a training set can only be reproduced if the exact bytes served on the day
it was built still exist. This script keeps them.

It does not join the two datasets, interpret a rating, derive a label, or drop
a record. That belongs in later steps, run against this snapshot, not here.

What is kept:

  * every response page as the raw bytes the server sent. Pages are never
    parsed and written back out, so the sha256 in the manifest is the digest of
    what was received, not of a re-serialisation of it;
  * manifest.json: how each page was requested, its status, size, digest and
    record count, the API-reported total, the sum of records, distinct ids, the
    distinct IEG as_of_date values, timestamps, the tool's git SHA, and a
    snapshot digest over every page digest.

What makes the run fail (no final directory is created):

  * a page with no records before the reported total has been reached;
  * a total that changes between pages: the dataset moved during the run and
    the pages would mix two states of it;
  * records that do not add up to the reported total at the end. A server that
    silently caps the page size would otherwise leave a gap that looks complete;
  * a body that is not JSON, or lacks the expected fields;
  * an output directory that already exists and is not empty, or a leftover
    partial directory from an earlier run.

Paging. IEG: top=1000&skip=N. P&O: rows=500&os=N. Each request advances the
offset by the requested page size; paging stops when the offset reaches the
total. The P&O total is a string in the API and is read as an integer.

Fields. The P&O request keeps the API's default field set. Asking for fl=*
returns about 300 fields per project, and a measured page of 10 projects came
to about 2.8 MB, which would put the full portfolio in the gigabytes. A fuller
pull is a separate decision and would be a different snapshot.

Politeness. At most one request per second. HTTP 429, HTTP 5xx and connection
errors are retried, up to three attempts in all, with waits of 60 s and then
120 s; after the third failed attempt the run stops and names the dataset and
page. Other 4xx responses are not retried.

Immutability. Everything is written into <out>.partial using exclusive-create
opens, and the directory is renamed to <out> only after every page and the
manifest are on disk. A failed run leaves <out>.partial in place for
inspection and creates no <out>; the next run refuses until that directory is
removed by hand.

Usage (from the repository root):

    python3 scripts/snapshot_worldbank_outcomes.py --out ../wb-snapshots/2026-10-15
    python3 scripts/snapshot_worldbank_outcomes.py --out ../wb-snapshots/x --only ieg
    python3 scripts/snapshot_worldbank_outcomes.py --out ../wb-snapshots/x --dry-run

--dry-run fetches the first page of each dataset, prints the API totals and the
first record's keys, and writes nothing.

Layout of <out>:

    manifest.json
    ieg/page-00000.json ...
    projects/page-00000.json ...
"""
import argparse
import datetime as _dt
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
TOOL_NAME = "scripts/snapshot_worldbank_outcomes.py"
REQUEST_TIMEOUT_S = 120
MIN_INTERVAL_S = 1.0  # at most one request per second
RETRY_ATTEMPTS = 3
BACKOFF_BASE_S = 60  # waits 60 s, then 120 s, then gives up
RETRYABLE_STATUS = frozenset({429, *range(500, 600)})
SNAPSHOT_DIGEST_METHOD = (
    "sha256 of the UTF-8 text made by sorting the lines '<page sha256>  <path "
    "relative to the snapshot directory>' over every page of every dataset and "
    "writing each one followed by a newline. manifest.json is not included."
)

# Read at call time, never bound as default arguments, so tests can replace them.
_clock = time.monotonic
_sleep = time.sleep

DATASETS = {
    "ieg": {
        "description": "World Bank IEG project performance ratings (datasetId DS00053, resourceId RS00055)",
        "base_url": "https://datacatalogapi.worldbank.org/dexapps/fone/api/apiservice",
        "fixed_params": {"datasetId": "DS00053", "resourceId": "RS00055", "type": "json"},
        "size_param": "top",
        "page_size": 1000,
        "offset_param": "skip",
        "total_key": "count",
        "records_key": "data",
        "records_are_mapping": False,
        "id_key": "project_id",
        "track_as_of_date": True,
    },
    "projects": {
        "description": "World Bank Projects & Operations (search.worldbank.org/api/v2/projects)",
        "base_url": "https://search.worldbank.org/api/v2/projects",
        "fixed_params": {"format": "json"},
        "size_param": "rows",
        "page_size": 500,
        "offset_param": "os",
        "total_key": "total",
        "records_key": "projects",
        "records_are_mapping": True,
        "id_key": None,  # the record id is the mapping key
        "track_as_of_date": False,
    },
}


class SnapshotError(RuntimeError):
    """The snapshot cannot be finished honestly. The message says what, and where."""


def _utc_now():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(*args):
    try:
        done = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip()


def _tool_info():
    sha = _git("rev-parse", "HEAD")
    porcelain = _git("status", "--porcelain")
    return {
        "name": TOOL_NAME,
        "git_sha": sha or None,
        "git_dirty": None if porcelain is None else porcelain != "",
        "python": platform.python_version(),
    }


def _as_int(value, label, what):
    if isinstance(value, bool):
        raise SnapshotError(f"{label}: {what} is a boolean, not a count")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise SnapshotError(f"{label}: {what} is not a count: {value!r}")


def _parse_page(name, body, label):
    spec = DATASETS[name]
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SnapshotError(f"{label}: body is not JSON ({type(exc).__name__}: {exc})") from None
    if not isinstance(payload, dict):
        raise SnapshotError(f"{label}: top level is not a JSON object")
    total_key = spec["total_key"]
    if total_key not in payload:
        raise SnapshotError(f"{label}: response has no '{total_key}' field")
    total = _as_int(payload[total_key], label, f"'{total_key}'")

    records_key = spec["records_key"]
    container = payload.get(records_key)
    if spec["records_are_mapping"]:
        if not isinstance(container, dict):
            raise SnapshotError(f"{label}: '{records_key}' is not an object")
        records = list(container.values())
        ids = list(container.keys())
    else:
        if not isinstance(container, list):
            raise SnapshotError(f"{label}: '{records_key}' is not a list")
        records = container
        ids = [r.get(spec["id_key"]) if isinstance(r, dict) else None for r in records]
    if not all(isinstance(r, dict) for r in records):
        raise SnapshotError(f"{label}: a record is not a JSON object")

    as_of = []
    if spec["track_as_of_date"]:
        as_of = sorted({str(r["as_of_date"]) for r in records if r.get("as_of_date") is not None})
    return {
        "total": total,
        "count": len(records),
        "ids": ids,
        "as_of_dates": as_of,
        "first_keys": list(records[0].keys()) if records else [],
    }


class _Client:
    """Every request goes through here: the one-per-second pacing and the retry policy."""

    def __init__(self):
        self._last_start = None

    def _pace(self):
        now = _clock()
        if self._last_start is not None:
            wait = MIN_INTERVAL_S - (now - self._last_start)
            if wait > 0:
                _sleep(wait)
                now = _clock()
        self._last_start = now

    def get(self, label, url, params):
        """Return the raw body of a 200 response, retrying as the module describes."""
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            self._pace()
            try:
                response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_S)
            except requests.RequestException as exc:
                reason = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code == 200:
                    return bytes(response.content)
                if response.status_code not in RETRYABLE_STATUS:
                    raise SnapshotError(f"{label}: HTTP {response.status_code}; not retried")
                reason = f"HTTP {response.status_code}"
            if attempt == RETRY_ATTEMPTS:
                raise SnapshotError(f"{label}: {reason} on all {RETRY_ATTEMPTS} attempts; giving up")
            wait = BACKOFF_BASE_S * 2 ** (attempt - 1)
            print(
                f"{label}: {reason}; attempt {attempt} of {RETRY_ATTEMPTS} failed, "
                f"retrying in {wait} s",
                file=sys.stderr,
            )
            _sleep(wait)


def _request_page(name, client, index, offset):
    spec = DATASETS[name]
    params = {
        **spec["fixed_params"],
        spec["size_param"]: spec["page_size"],
        spec["offset_param"]: offset,
    }
    label = f"{name} page {index} ({spec['offset_param']}={offset})"
    body = client.get(label, spec["base_url"], params)
    return label, params, body, _parse_page(name, body, label)


def probe(names=None):
    """Fetch page 0 of each dataset and report it. Writes nothing."""
    client = _Client()
    report = {}
    for name in names or DATASETS:
        label, _params, _body, page = _request_page(name, client, 0, 0)
        if page["total"] <= 0 or page["count"] == 0:
            raise SnapshotError(
                f"{label}: total {page['total']}, page holds {page['count']} records; "
                "nothing to snapshot"
            )
        report[name] = {
            "api_total": page["total"],
            "page0_records": page["count"],
            "first_record_keys": page["first_keys"],
        }
    return report


def _collect(name, client, write):
    spec = DATASETS[name]
    pages = []
    seen_ids = set()
    as_of = set()
    total = None
    records_sum = 0
    without_id = 0
    offset = 0
    index = 0
    while True:
        label, params, body, page = _request_page(name, client, index, offset)
        if total is None:
            if page["total"] <= 0:
                raise SnapshotError(
                    f"{label}: the API reports a total of {page['total']}; nothing to snapshot"
                )
            total = page["total"]
        elif page["total"] != total:
            raise SnapshotError(
                f"{label}: the API's total changed from {total} to {page['total']} during "
                "the run; refusing to mix two states of the dataset"
            )
        # The loop only runs while offset < total, so an empty page here is always early.
        if page["count"] == 0:
            raise SnapshotError(
                f"{label}: empty page before the reported total of {total} was reached"
            )

        relpath = f"{name}/page-{index:05d}.json"
        write(relpath, body)
        pages.append({
            "file": relpath,
            "params": params,
            "http_status": 200,
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "records": page["count"],
            "api_total": page["total"],
        })
        records_sum += page["count"]
        without_id += sum(1 for i in page["ids"] if i in (None, ""))
        seen_ids.update(str(i) for i in page["ids"] if i not in (None, ""))
        as_of.update(page["as_of_dates"])
        print(f"{label}: {page['count']} records, {len(body)} bytes", file=sys.stderr)

        index += 1
        offset += spec["page_size"]
        if offset >= total:
            break

    if records_sum != total:
        raise SnapshotError(
            f"{name}: the pages hold {records_sum} records but the API reports a total of "
            f"{total}; refusing to write a snapshot with a gap or an overlap"
        )
    dataset = {
        "description": spec["description"],
        "base_url": spec["base_url"],
        "fixed_params": spec["fixed_params"],
        "page_size_param": spec["size_param"],
        "page_size": spec["page_size"],
        "offset_param": spec["offset_param"],
        "api_total": total,
        "sum_records": records_sum,
        "unique_ids": len(seen_ids),
        "records_without_id": without_id,
        "pages": pages,
    }
    if spec["track_as_of_date"]:
        dataset["as_of_dates"] = sorted(as_of)
    return dataset


def _snapshot_digest(datasets):
    lines = sorted(
        f"{page['sha256']}  {page['file']}"
        for dataset in datasets.values()
        for page in dataset["pages"]
    )
    return hashlib.sha256("".join(line + "\n" for line in lines).encode("utf-8")).hexdigest()


def _write_new(path, body):
    """Create a file that must not already exist, and flush it to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as fh:
        fh.write(body)
        fh.flush()
        os.fsync(fh.fileno())


def _refuse_existing(out, partial):
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise SnapshotError(
            f"refusing to write: {out} already exists and is not an empty directory. "
            "A snapshot is immutable; choose a new --out."
        )
    if partial.exists():
        raise SnapshotError(
            f"refusing to write: {partial} was left by an earlier run that did not finish. "
            "Inspect it, then remove it by hand."
        )


def take_snapshot(out, names=None):
    """Fetch the datasets into a new directory <out>; return the manifest.

    Raises SnapshotError, leaving <out>.partial in place, if anything is wrong.
    """
    out = Path(out)
    partial = out.with_name(out.name + ".partial")
    names = list(names or DATASETS)
    _refuse_existing(out, partial)

    started = _utc_now()
    client = _Client()
    out.parent.mkdir(parents=True, exist_ok=True)
    partial.mkdir()
    try:
        datasets = {}
        for name in names:
            datasets[name] = _collect(
                name, client, lambda rel, body: _write_new(partial / rel, body)
            )
        manifest = {
            "tool": _tool_info(),
            "started_utc": started,
            "finished_utc": _utc_now(),
            "datasets": datasets,
            "snapshot_digest": _snapshot_digest(datasets),
            "snapshot_digest_method": SNAPSHOT_DIGEST_METHOD,
        }
        manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        _write_new(partial / "manifest.json", manifest_bytes)
        if out.exists():
            out.rmdir()  # empty: _refuse_existing allowed nothing else
        os.rename(partial, out)
    except BaseException:
        print(
            f"snapshot did not finish; {partial} is left in place for inspection",
            file=sys.stderr,
        )
        raise
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Snapshot the World Bank IEG ratings and P&O projects as raw, immutable pages."
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="directory to create; must not exist, or must be empty. Written atomically.",
    )
    parser.add_argument(
        "--only",
        choices=sorted(DATASETS),
        help="snapshot only this dataset",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch page 0 of each dataset, print the totals and first-record keys, write nothing",
    )
    args = parser.parse_args(argv)
    names = [args.only] if args.only else list(DATASETS)
    try:
        if args.dry_run:
            for name, info in probe(names).items():
                print(f"{name}: API total {info['api_total']}; page 0 holds {info['page0_records']} records")
                print(f"{name}: first record keys: {', '.join(info['first_record_keys'])}")
            print("dry run: nothing written")
            return 0
        manifest = take_snapshot(args.out, names)
    except SnapshotError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"snapshot written: {args.out}")
    print(f"snapshot digest: {manifest['snapshot_digest']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
