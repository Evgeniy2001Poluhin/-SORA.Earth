"""Synthetic fixtures for scripts/ieg_target_r1.py. Nothing here reads real data.

make_world() builds, as bytes, an IEG snapshot in the snapshot-manifest layout,
the r2 structural receipt (candidates csv, split id files, manifest), the
vocabulary and the D1 policy, and the Pins that match them. Every hash is
computed from the bytes made here, so the pin checks run as they do on real
inputs.

CLI (synthetic only; the builder itself has no synthetic mode):
    python tests/r1_synthetic_harness.py --out DIR [--scenario ok|blocked]
runs validate_bytes, build and write_outputs into DIR. Exit 0 ok, 4 blocked.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import io
import json
import random
import sys
from dataclasses import dataclass, field
from dataclasses import replace
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

_BUILDER = Path(__file__).resolve().parents[1] / "scripts" / "ieg_target_r1.py"


def _load_builder():
    if "ieg_target_r1" in sys.modules:
        return sys.modules["ieg_target_r1"]
    spec = importlib.util.spec_from_file_location("ieg_target_r1", _BUILDER)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ieg_target_r1"] = module
    spec.loader.exec_module(module)
    return module


r1 = _load_builder()

ABSENT = object()
SNAP_AS_OF_RAW = "13-Oct-2026"
FORBIDDEN_MARKERS = {
    "bank_performance": "MARKER-BANK-7431",
    "quality_at_entry": "MARKER-QAE-2208",
    "me_quality": "MARKER-ME-5519",
    "evaluation_type": "MARKER-ETYPE-0097",
    "lending_instrument_type": "MARKER-LIT-3316",
    "wb_region": "MARKER-REGION-8842",
    "approval_fy": 1999,
}
DEFAULT_VOCAB = {
    "map": {
        "Highly Satisfactory": 1,
        "Satisfactory": 1,
        "Moderately Satisfactory": 1,
        "Moderately Unsatisfactory": 0,
        "Unsatisfactory": 0,
        "Highly Unsatisfactory": 0,
    },
    "not_rated": ["Not Rated"],
}
DEFAULT_D1 = {"cohort": "development", "min_G": 2, "max_largest_share": 0.6, "min_G_eff": 2.0}
SYNTH_PERMIT = b"synthetic permit: not a signed R1 permit"
SYNTH_CAPACITY = 4 * 1024 * 1024


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def new_seal():
    """A throwaway X25519 key pair in memory; the private key stays in the test process."""
    private = X25519PrivateKey.generate()
    public = private.public_key().public_bytes(encoding=serialization.Encoding.Raw,
                                               format=serialization.PublicFormat.Raw)
    return r1.SealParams(recipient_public=public, permit_bytes=SYNTH_PERMIT, capacity=SYNTH_CAPACITY), private


@dataclass
class World:
    blobs: dict
    pins: object
    placed: list = field(default_factory=list)  # (pid, fy, page_file, index)
    rows: list = field(default_factory=list)
    split: dict = field(default_factory=dict)
    seal: object = None
    private_key: object = None


def make_world(projects, *, page_size=2, shuffle_seed=None, vocab=None, d1=None, receipt_hook=None):
    """projects: [{"pid", "country", "candidate"(default True), "records": [{"fy", "outcome"|ABSENT, ...}]}].

    Record keys: "fy" (int or "fy_raw" string), "outcome" (any JSON value, or ABSENT to omit the key),
    "as_of" (optional raw string). Every record also carries the FORBIDDEN_MARKERS keys.
    shuffle_seed permutes the records and so regenerates every page, locator, hash and pin.
    receipt_hook(rows, split) may edit the candidate rows and split id lists before they are written.
    """
    flat = []
    for proj in projects:
        for rec in proj["records"]:
            flat.append((proj["pid"], rec))
    if shuffle_seed is not None:
        random.Random(shuffle_seed).shuffle(flat)

    blobs = {}
    page_entries = []
    placed = []
    for n, start in enumerate(range(0, len(flat), page_size)):
        name = f"ieg/page-{n:05d}.json"
        chunk = flat[start:start + page_size]
        data = []
        for index, (pid, rec) in enumerate(chunk):
            item = {
                "project_id": pid,
                "evaluation_fy": rec.get("fy_raw", str(rec.get("fy"))),
                "as_of_date": rec.get("as_of", SNAP_AS_OF_RAW),
            }
            if rec.get("outcome", ABSENT) is not ABSENT:
                item["outcome"] = rec["outcome"]
            item.update(FORBIDDEN_MARKERS)
            data.append(item)
            placed.append((pid, rec.get("fy"), name, index))
        page_bytes = json.dumps({"count": len(flat), "data": data}, ensure_ascii=False, sort_keys=True).encode("utf-8")
        blobs[f"snapshot/{name}"] = page_bytes
        page_entries.append({"file": name, "sha256": sha(page_bytes), "records": len(data)})

    lines = sorted(f"{e['sha256']}  {e['file']}" for e in page_entries)
    digest = sha("".join(line + "\n" for line in lines).encode("utf-8"))
    snap_manifest = {
        "datasets": {"ieg": {"api_total": len(flat), "pages": page_entries}},
        "snapshot_digest": digest,
        "snapshot_digest_method": "sha256 of sorted 'sha256  file' lines",
    }
    snap_bytes = json.dumps(snap_manifest, sort_keys=True, indent=1).encode("utf-8")
    blobs[r1.K_SNAP_MANIFEST] = snap_bytes

    # Receipt r2 as the reference structural script writes it.
    by_pid: dict[str, list] = {}
    for pid, fy, file, index in placed:
        by_pid.setdefault(pid, []).append((fy, file, index))
    rows = []
    split = {"development": [], "holdout": []}
    for proj in projects:
        if not proj.get("candidate", True):
            continue
        recs = by_pid[proj["pid"]]
        max_fy = max(fy for fy, _, _ in recs)
        window = "development" if max_fy <= 2019 else "holdout"
        locs = sorted(f"{file}#{index}" for fy, file, index in recs if fy == max_fy)
        rows.append({
            "project_id": proj["pid"],
            "window": window,
            "evaluation_fy": str(max_fy),
            "wb_code": proj["country"],
            "authoritative_records": ";".join(locs),
        })
        split[window].append(proj["pid"])
    for key in split:
        split[key] = sorted(split[key])
    if receipt_hook is not None:
        receipt_hook(rows, split)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(r1.CANDIDATE_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for row in sorted(rows, key=lambda r: r["project_id"]):
        writer.writerow(row)
    struct_files = {
        "structural_candidates.csv": buf.getvalue().encode("utf-8"),
        "split_ids_development.txt": "".join(f"{p}\n" for p in split["development"]).encode("utf-8"),
        "split_ids_holdout.txt": "".join(f"{p}\n" for p in split["holdout"]).encode("utf-8"),
    }
    for name, data in struct_files.items():
        blobs[f"structural/{name}"] = data
    struct_manifest = {
        "rule_version": "1",
        "outputs": {name: {"bytes": len(d), "sha256": sha(d)} for name, d in struct_files.items()},
    }
    struct_bytes = json.dumps(struct_manifest, sort_keys=True, indent=1).encode("utf-8")
    blobs[r1.K_STRUCT_MANIFEST] = struct_bytes

    vocab_bytes = json.dumps(vocab or DEFAULT_VOCAB, sort_keys=True, indent=1).encode("utf-8")
    d1_bytes = json.dumps(d1 or DEFAULT_D1, sort_keys=True, indent=1).encode("utf-8")
    blobs[r1.K_VOCAB] = vocab_bytes
    blobs[r1.K_D1] = d1_bytes

    pins = r1.Pins(
        snapshot_manifest_sha=sha(snap_bytes),
        snapshot_page_digest=digest,
        structural_manifest_sha=sha(struct_bytes),
        vocabulary_sha=sha(vocab_bytes),
        d1_policy_sha=sha(d1_bytes),
    )
    seal, private = new_seal()
    return World(blobs=blobs, pins=pins, placed=placed, rows=rows, split=split, seal=seal, private_key=private)


def run_pipeline(world_or_blobs, pins=None):
    """validate_bytes -> build, returning the Result. Synthetic run metadata, no git call."""
    blobs = world_or_blobs.blobs if isinstance(world_or_blobs, World) else world_or_blobs
    pins = world_or_blobs.pins if isinstance(world_or_blobs, World) else pins
    inputs = r1.validate_bytes(blobs, pins)
    seal = world_or_blobs.seal if isinstance(world_or_blobs, World) else None
    inputs = replace(inputs, run_meta=r1.make_run_meta("synthetic", git=False), seal=seal)
    return r1.build(inputs)


DEMO_PROJECTS = [
    {"pid": "P000001", "country": "ARG", "records": [{"fy": 2012, "outcome": "Satisfactory"}]},
    {"pid": "P000002", "country": "ARG", "records": [{"fy": 2015, "outcome": "Unsatisfactory"}]},
    {"pid": "P000003", "country": "BRA", "records": [{"fy": 2016, "outcome": "Highly Satisfactory"}]},
    {"pid": "P000004", "country": "CHL", "records": [{"fy": 2018, "outcome": "Moderately Unsatisfactory"}]},
    {"pid": "P000005", "country": "BRA", "records": [{"fy": 2021, "outcome": "Moderately Satisfactory"}]},
    {"pid": "P000006", "country": "CHL", "records": [{"fy": 2023, "outcome": "Highly Unsatisfactory"}]},
    {"pid": "P000007", "country": "PER", "records": [{"fy": 2024, "outcome": "Not Rated"}]},
]
BLOCKED_EXTRA = {"pid": "P000008", "country": "PER", "records": [{"fy": 2024, "outcome": "Excellent"}]}


def demo_world(scenario="ok"):
    projects = list(DEMO_PROJECTS)
    if scenario == "blocked":
        projects.append(BLOCKED_EXTRA)
    return make_world(projects)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Synthetic R1 run. Never reads real data.")
    parser.add_argument("--out", required=True)
    parser.add_argument("--scenario", choices=("ok", "blocked"), default="ok")
    args = parser.parse_args(argv)
    world = demo_world(args.scenario)
    inputs = r1.validate_bytes(world.blobs, world.pins)
    inputs = replace(inputs, run_meta=r1.make_run_meta("synthetic", git=False), seal=world.seal)
    result = r1.build(inputs)
    final = r1.write_outputs(result, Path(args.out))
    print(final)
    return 0 if result.status == "ok" else 4


if __name__ == "__main__":
    sys.exit(main())
