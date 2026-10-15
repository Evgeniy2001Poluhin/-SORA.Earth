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
    count of identical records (always zero in a written snapshot), the distinct
    IEG as_of_date values, timestamps, the tool's git SHA, and a snapshot digest
    over every page digest.

What makes the run fail (no final directory is created):

  * a page with no records before the reported total has been reached;
  * a total that changes between pages: the dataset moved during the run and
    the pages would mix two states of it;
  * records that do not add up to the reported total at the end. A server that
    silently caps the page size would otherwise leave a gap that looks complete;
  * a P&O snapshot whose records do not carry distinct ids. The sum check
    cannot see this: a page that repeats another page keeps the sum equal to the
    total while other records are missed (see Paging below);
  * a record that breaks its dataset's contract: an IEG record without a
    non-empty string project_id or as_of_date, or a P&O record whose own id is
    not a non-empty string equal to its key. Nothing is coerced or repaired;
  * an IEG snapshot whose records carry more than one as_of_date. That mixes
    two vintages of the publisher's ratings;
  * a record identical to an earlier one. The tool cannot tell an overlap
    between pages from a publisher duplicate, so it refuses both rather than
    claim which one this is;
  * an IEG total larger than the one request IEG is read with (see Paging);
  * a body that is not JSON, or lacks the expected fields;
  * an output directory that already exists and is not empty, or a leftover
    partial directory from an earlier run;
  * an fsync that fails before the rename. The run is not durable, so it stops
    and creates no <out>.

Paging. IEG is read in one request, top=50000&skip=0. The publisher documents no
total order for top and skip, so paging IEG can hide a lost record: a page can
repeat project A with a changed field while project C falls between the pages,
and no count or hash check sees it. One response is one consistent read. On
2026-10-15 a single request with top=15000 returned all 12627 records in one
11 MB response, with the same canonical-record multiset as a 13-page read, and
as_of_date 13-Oct-2026 on every record. If the reported total ever exceeds one
request, the run refuses rather than pages.

P&O: rows=500&os=N, with srt=id&order=asc. Each request advances the offset by
the requested page size; paging stops when the offset reaches the total. The
P&O total is a string in the API and is read as an integer.

The explicit sort is not optional. Without it the P&O default order is not
stable across pages. The 2026-10-15 run measured 147 records returned twice,
on adjacent pages, which means 147 other records were missed, and the record
sum still matched the total. Sorting on id, which is unique, gives the paging
a total order. The distinct-id check is what refuses a run where that fails.

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
manifest are on disk. A directory fsync makes the entries in that directory
durable, and no others. So each dataset directory is synced once its pages are
written (partial/ieg, partial/projects), then <out>.partial after manifest.json,
then the rename, then <out>.parent. A directory the run creates above <out> has
its entry in its parent, so each such parent is synced, bottom-up, before
anything is written; ancestors that already existed and hold no new entry are
left alone. A failed run leaves <out>.partial in place for inspection and
creates no <out>; the next run refuses until that directory is removed by hand.
An fsync that fails after the rename leaves <out> in place and is reported as an
unconfirmed snapshot directory. Nothing is deleted automatically.

The manifest's own sha256 is printed on stdout, next to the snapshot digest. It
is not stored inside the manifest, which cannot contain its own digest.

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

def _check_ieg_records(label, records):
    """The IEG record contract: a usable project_id and as_of_date on every record.

    Nothing is coerced. A number or an object where a string belongs is a refusal.
    """
    for index, record in enumerate(records):
        project_id = record.get("project_id")
        if not isinstance(project_id, str) or not project_id:
            raise SnapshotError(
                f"{label}: record {index} has no usable project_id ({project_id!r}); "
                "every IEG record must carry a non-empty string project_id"
            )
        as_of = record.get("as_of_date")
        if not isinstance(as_of, str) or not as_of:
            raise SnapshotError(
                f"{label}: record {index} ({project_id}) has no usable as_of_date ({as_of!r})"
            )


def _check_projects_records(label, mapping):
    """The P&O id contract: each record's own id is a non-empty string equal to its outer key.

    Nothing is coerced or repaired. The page and the key are named in the message.
    """
    for key, record in mapping.items():
        inner = record.get("id")
        if not isinstance(inner, str) or not inner or inner != key:
            raise SnapshotError(
                f"{label}: the record under key {key!r} has id {inner!r}; "
                "a P&O record's id must be a non-empty string equal to its key"
            )


DATASETS = {
    "ieg": {
        "description": "World Bank IEG project performance ratings (datasetId DS00053, resourceId RS00055)",
        "base_url": "https://datacatalogapi.worldbank.org/dexapps/fone/api/apiservice",
        "fixed_params": {"datasetId": "DS00053", "resourceId": "RS00055", "type": "json"},
        "size_param": "top",
        "page_size": 50000,  # one request; see Paging in the module docstring
        "one_request": True,
        "offset_param": "skip",
        "total_key": "count",
        "records_key": "data",
        "records_are_mapping": False,
        "id_key": "project_id",
        "ids_unique": False,  # IEG legitimately repeats some project_ids
        "track_as_of_date": True,
        "record_contract": _check_ieg_records,
    },
    "projects": {
        "description": "World Bank Projects & Operations (search.worldbank.org/api/v2/projects)",
        "base_url": "https://search.worldbank.org/api/v2/projects",
        # The explicit sort is what makes the paging stable; see the module docstring.
        "fixed_params": {"format": "json", "srt": "id", "order": "asc"},
        "size_param": "rows",
        "page_size": 500,
        "offset_param": "os",
        "total_key": "total",
        "records_key": "projects",
        "records_are_mapping": True,
        "id_key": None,  # the record id is the mapping key
        "ids_unique": True,
        "one_request": False,
        "track_as_of_date": False,
        "record_contract": _check_projects_records,
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
    spec["record_contract"](label, container)

    as_of = []
    if spec["track_as_of_date"]:
        as_of = sorted({r["as_of_date"] for r in records})  # validated: non-empty strings
    # Canonical form, so two records are "identical" when their content is, whatever the key order.
    digests = [
        hashlib.sha256(json.dumps(r, sort_keys=True).encode("utf-8")).hexdigest() for r in records
    ]
    return {
        "total": total,
        "count": len(records),
        "ids": ids,
        "digests": digests,
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
    seen_digests = set()
    duplicate_records = 0
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
            if spec["one_request"] and page["total"] > spec["page_size"]:
                raise SnapshotError(
                    f"{label}: the API reports {page['total']} records, more than the one request "
                    f"of {spec['page_size']} that {name} is read with. The publisher documents no "
                    f"total order for {spec['offset_param']}, so paging {name} could hide a lost "
                    "record. Refusing rather than paging."
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
        for digest in page["digests"]:
            if digest in seen_digests:
                duplicate_records += 1
            else:
                seen_digests.add(digest)
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
    # Checked after the sum: a repeated page keeps the sum right and only shows here.
    if spec["ids_unique"] and (len(seen_ids) != total or without_id):
        raise SnapshotError(
            f"{name}: the pages hold {total} records but {len(seen_ids)} distinct ids "
            f"({without_id} without an id); the paging was not stable, so records repeated "
            "or were missed. Refusing to write a snapshot."
        )
    # Runs after the id check, so a repeated P&O id is reported as such.
    if duplicate_records:
        raise SnapshotError(
            f"{name}: {duplicate_records} records are identical to an earlier record in this "
            "snapshot. The tool cannot tell an overlap between pages from a publisher duplicate, "
            "so it refuses both. Refusing to write a snapshot."
        )
    if spec["track_as_of_date"] and len(as_of) > 1:
        raise SnapshotError(
            f"{name}: the records carry {len(as_of)} distinct as_of_date values "
            f"({', '.join(sorted(as_of))}); the snapshot mixes vintages of the publisher's "
            "ratings. Refusing to write a snapshot."
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
        "duplicate_records": duplicate_records,
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


def _fsync_dir(path):
    """Flush a directory's entries to disk. An OSError propagates: a run that cannot do this is not durable."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _flush_dir(path):
    """_fsync_dir before the rename. Its OSError is a refusal, reported as one, not a traceback."""
    try:
        _fsync_dir(path)
    except OSError as exc:
        raise SnapshotError(
            f"could not flush {path} to disk ({exc}); the snapshot is not durable"
        ) from exc


def _missing_dirs(path):
    """The directories at path and above it that do not exist yet, deepest first."""
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    return missing


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
    # Each directory this run creates above <out> has its entry in its parent, so
    # the parents are synced, deepest first, before anything is written.
    created = _missing_dirs(out.parent)
    out.parent.mkdir(parents=True, exist_ok=True)
    for new_dir in created:
        _flush_dir(new_dir.parent)
    partial.mkdir()
    try:
        datasets = {}
        for name in names:
            datasets[name] = _collect(
                name, client, lambda rel, body: _write_new(partial / rel, body)
            )
            _flush_dir(partial / name)  # the page entries live in the dataset directory
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
        _flush_dir(partial)  # the entries for the dataset directories and manifest.json
        if out.exists():
            out.rmdir()  # empty: _refuse_existing allowed nothing else
        os.rename(partial, out)
    except BaseException:
        print(
            f"snapshot did not finish; {partial} is left in place for inspection",
            file=sys.stderr,
        )
        raise
    # Outside the try: once the rename has happened, <partial> no longer exists and
    # must not be named as the thing left behind.
    try:
        _fsync_dir(out.parent)  # makes the rename itself durable
    except OSError as exc:
        raise SnapshotError(
            f"{out} was written and renamed into place, but fsync of {out.parent} failed "
            f"({exc}). The snapshot directory exists; its durability is unconfirmed. "
            "Nothing was deleted."
        ) from exc
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
    manifest_sha = hashlib.sha256((Path(args.out) / "manifest.json").read_bytes()).hexdigest()
    print(f"snapshot written: {args.out}")
    print(f"snapshot digest: {manifest['snapshot_digest']}")
    print(f"manifest sha256: {manifest_sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
