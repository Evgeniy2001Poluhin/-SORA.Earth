#!/usr/bin/env python3
"""Build the map's per-country inputs from a pinned snapshot, offline.

scripts/snapshot_map_sources.py fetches the sources once and keeps the bytes.
This script reads only that snapshot directory: no network, no database, no
BENCHMARKS. It writes one table with, for each of the 33 map countries and each
of the five inputs, either the value with its provenance or a missing value
with the reason.

Verification. Before anything is read, the snapshot's manifest is checked:
every listed file exists and has the recorded size and sha256; no raw file is
unlisted; the snapshot digest recomputes; the directory name is the manifest's
snapshot date. Then every raw World Bank page and the UNDP CSV are parsed again
with the snapshot script's own validators, so the same refusals apply here. The
manifest's latest-year figures must agree with what the raw bytes say, or the
build refuses.

Selection rule (the policy is written into the output):

  * For each input and country, the value is the one for the LATEST year that
    has a non-null value. No earlier year is ever used in its place.
  * Age is the snapshot year minus that year. If it is above the input's maximum
    age the value is missing, with reason "too_old" and the year it was found at.
  * If the country has no non-null value at all the reason is "no_observation".
  * Nothing is substituted: not another indicator, not the static BENCHMARKS,
    not the global average, not a default.

Maximum ages (years, measured against the snapshot's own year):

    gini_index          10   survey-based, sparse; a longer window would admit
                             surveys too old to describe the present
    gov_effectiveness    3   WGI is published yearly; three years is the window
                             in which the latest estimate is still current
    hdi                  3   UNDP publishes annually
    renewable_share      5   the renewables and CO2 series are annual with a
                             lag of one to three years; five keeps every country
                             that the source currently reports
    co2_per_capita       5   as above

Outputs, in a NEW directory (nothing is overwritten):

    map_inputs.json   the table: countries, each input with value, unit, year,
                      age, source, release and fetched_at, or the missing reason
    manifest.json     the sha256 and size of map_inputs.json, the snapshot
                      manifest sha256 it was built from, and per-input counts

Output contains no wall-clock time and no absolute path, so two builds from the
same snapshot are byte-identical.

Usage (from the repository root):

    python3 scripts/build_map_inputs.py --snapshot <root>/<date> --out <root>/inputs-<date>
    python3 scripts/build_map_inputs.py --latest-of <root> --out <dir>

--latest-of picks the newest directory named YYYY-MM-DD under <root> that
verifies. A *.partial or *.failed-* directory is never a candidate, so a failed
or refused snapshot run leaves the previous verified snapshot as the one used.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import snapshot_map_sources as snap  # noqa: E402

BUILD_SCHEMA = "map-inputs/v1"
OUTPUT_NAME = "map_inputs.json"
MANIFEST_NAME = "manifest.json"
DIR_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# snapshot key -> the input name the map uses
INPUT_FOR_KEY = {
    "renewables": "renewable_share",
    "co2_per_capita": "co2_per_capita",
    "gini": "gini_index",
    "gov_effectiveness": "gov_effectiveness",
    "hdi": "hdi",
}
INPUT_ORDER = ("renewable_share", "co2_per_capita", "gini_index", "gov_effectiveness", "hdi")
MAX_AGE_YEARS = {
    "gini_index": 10,
    "gov_effectiveness": 3,
    "hdi": 3,
    "renewable_share": 5,
    "co2_per_capita": 5,
}


class BuildError(RuntimeError):
    """The snapshot cannot be built from honestly. The message says what, and where."""


def _sha256(body):
    return hashlib.sha256(body).hexdigest()


def verify_snapshot(snapshot_dir):
    """Check the manifest and every file it lists. Returns (manifest, manifest_sha256)."""
    snapshot_dir = Path(snapshot_dir)
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.is_file():
        raise BuildError(f"{snapshot_dir}: no manifest.json; not a finished snapshot")
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BuildError(f"{manifest_path}: not JSON ({exc})") from None

    if snapshot_dir.name != manifest.get("snapshot_date_utc"):
        raise BuildError(
            f"{snapshot_dir}: the directory is not named for the manifest's snapshot date "
            f"{manifest.get('snapshot_date_utc')!r}"
        )
    if manifest.get("countries_iso3") != list(snap.MAP_ISO3):
        raise BuildError(f"{snapshot_dir}: the manifest's country list is not the map's 33 countries")

    listed = manifest.get("files")
    if not isinstance(listed, list) or not listed:
        raise BuildError(f"{snapshot_dir}: the manifest lists no files")
    seen = set()
    for entry in listed:
        rel = entry["path"]
        if rel in seen or Path(rel).is_absolute() or ".." in Path(rel).parts:
            raise BuildError(f"{snapshot_dir}: the manifest lists {rel!r} twice or outside the snapshot")
        seen.add(rel)
        path = snapshot_dir / rel
        if not path.is_file():
            raise BuildError(f"{snapshot_dir}: listed file {rel} is missing")
        body = path.read_bytes()
        if len(body) != entry["bytes"] or _sha256(body) != entry["sha256"]:
            raise BuildError(f"{snapshot_dir}: {rel} does not match the manifest (size or sha256)")

    raw_dir = snapshot_dir / "raw"
    if raw_dir.exists():
        for path in raw_dir.rglob("*"):
            if path.is_file() and path.relative_to(snapshot_dir).as_posix() not in seen:
                raise BuildError(f"{snapshot_dir}: {path.name} is in the snapshot but not in the manifest")

    if snap._snapshot_digest(listed) != manifest.get("snapshot_digest"):
        raise BuildError(f"{snapshot_dir}: the snapshot digest does not recompute from the listed files")
    return manifest, _sha256(manifest_bytes)


def latest_verified_snapshot(root):
    """The newest verified snapshot under root, or None. Partial and failed directories are never candidates."""
    root = Path(root)
    if not root.is_dir():
        return None
    candidates = sorted(
        (p for p in root.iterdir() if p.is_dir() and DIR_NAME.match(p.name)),
        key=lambda p: p.name,
        reverse=True,
    )
    for candidate in candidates:
        try:
            verify_snapshot(candidate)
        except (BuildError, OSError, KeyError, ValueError):
            continue
        return candidate
    return None


def _spec_for(manifest, key):
    """The snapshot script's own spec for one snapshot key (range, name token, source)."""
    plan = snap.build_plan(manifest["renewables_choice"], [key])
    if len(plan) != 1:
        raise BuildError(f"no specification for snapshot series {key!r}")
    return plan[0][3]


def _wb_observations(snapshot_dir, manifest, key):
    """{iso3: {year: observation}} for one World Bank series, from its raw pages, re-checked."""
    entry = manifest["indicators"][key]
    spec = _spec_for(manifest, key)
    pages = []
    for request in entry["requests"]:
        rel = f"raw/{key}/page-{request['page']:05d}.json"
        body = (snapshot_dir / rel).read_bytes()
        parsed = snap.parse_wb_page(body, f"{key} page {request['page']}", entry["code"], spec, set(snap.MAP_ISO3))
        if len(parsed["rows"]) != request["rows"] or parsed["total"] != request["api_total"]:
            raise BuildError(f"{key} page {request['page']}: the raw bytes disagree with the manifest")
        pages.append((request, parsed))
    rows_by_page = [parsed for _, parsed in pages]
    snap.assemble_wb_series(rows_by_page, key)

    observations = {}
    for request, parsed in pages:
        for row in parsed["rows"]:
            observations.setdefault(row["iso3"], {})[row["year"]] = {
                "value": row["value"],
                "unit": entry["unit"],
                "source": f"World Bank {entry['code']} ({entry['metadata']['source_name']})",
                "release": f"World Bank lastupdated {parsed['lastupdated']}",
                "fetched_at": request["fetched_at"],
            }
    return observations


def _hdi_observations(snapshot_dir, manifest, key):
    """{iso3: {year: observation}} for the UNDP HDI, from the raw CSV, re-checked."""
    entry = manifest["indicators"][key]
    body = (snapshot_dir / "raw/hdi/composite_indices_time_series.csv").read_bytes()
    parsed = snap.parse_undp_csv(body, "hdi csv", set(snap.MAP_ISO3))
    observations = {}
    for iso3, by_year in parsed["table"].items():
        for year, value in by_year.items():
            observations.setdefault(iso3, {})[year] = {
                "value": value,
                "unit": entry["unit"],
                "source": "UNDP Human Development Report composite indices time series",
                "release": entry["release"],
                "fetched_at": entry["csv_fetched_at"],
            }
    return observations


def _observations(snapshot_dir, manifest, key):
    if manifest["indicators"][key]["kind"] == "undp_csv":
        return _hdi_observations(snapshot_dir, manifest, key)
    return _wb_observations(snapshot_dir, manifest, key)


def _check_manifest_latest(manifest, key, observations):
    """The manifest's latest-year figures must be what the raw bytes say."""
    recorded = manifest["indicators"][key]["latest_year_per_country"]
    for iso3 in snap.MAP_ISO3:
        present = [y for y, o in observations.get(iso3, {}).items() if o["value"] is not None]
        found = max(present) if present else None
        if recorded.get(iso3) != found:
            raise BuildError(
                f"{key} {iso3}: the manifest says the latest year is {recorded.get(iso3)!r}, "
                f"the raw bytes say {found!r}"
            )


def build_table(snapshot_dir):
    """The inputs table as a dict, built only from the verified snapshot directory."""
    snapshot_dir = Path(snapshot_dir)
    manifest, manifest_sha = verify_snapshot(snapshot_dir)
    for key in INPUT_FOR_KEY:
        if key not in manifest["indicators"]:
            raise BuildError(f"{snapshot_dir}: the snapshot holds no {key!r} series; it is not a map snapshot")

    snapshot_year = int(manifest["snapshot_date_utc"][:4])
    observations = {}
    for key in INPUT_FOR_KEY:
        observations[key] = _observations(snapshot_dir, manifest, key)
        _check_manifest_latest(manifest, key, observations[key])

    countries = []
    for iso3 in sorted(snap.MAP_ISO3):
        inputs = {}
        for key, name in INPUT_FOR_KEY.items():
            found = observations[key].get(iso3, {})
            present = {year: obs for year, obs in found.items() if obs["value"] is not None}
            if not present:
                inputs[name] = {"value": None, "reason": "no_observation"}
                continue
            latest = max(present)
            age = snapshot_year - int(latest)
            limit = MAX_AGE_YEARS[name]
            if age > limit:
                inputs[name] = {
                    "value": None,
                    "reason": "too_old",
                    "latest_year": latest,
                    "age_years": age,
                    "max_age_years": limit,
                }
                continue
            obs = present[latest]
            inputs[name] = {
                "value": obs["value"],
                "unit": obs["unit"],
                "year": latest,
                "age_years": age,
                "source": obs["source"],
                "release": obs["release"],
                "fetched_at": obs["fetched_at"],
            }
        missing = sorted(n for n in INPUT_ORDER if inputs[n]["value"] is None)
        countries.append({"iso3": iso3, "complete": not missing, "missing": missing, "inputs": inputs})

    present_counts = {n: sum(1 for c in countries if c["inputs"][n]["value"] is not None) for n in INPUT_ORDER}
    reasons = {}
    for c in countries:
        for n in INPUT_ORDER:
            reason = c["inputs"][n].get("reason")
            if reason:
                reasons.setdefault(n, {}).setdefault(reason, 0)
                reasons[n][reason] += 1
    return {
        "schema": BUILD_SCHEMA,
        "snapshot": {
            "date_utc": manifest["snapshot_date_utc"],
            "manifest_sha256": manifest_sha,
            "snapshot_digest": manifest["snapshot_digest"],
            "renewables_choice": manifest["renewables_choice"],
            "renewables_code": manifest["renewables_code"],
            "co2_code": manifest["co2_code"],
            "gov_effectiveness_code": manifest["gov_effectiveness_code"],
        },
        "policy": {
            "selection": "latest non-null year per country; no substitution, no fallback, no earlier year",
            "age_reference_year": snapshot_year,
            "max_age_years": {n: MAX_AGE_YEARS[n] for n in INPUT_ORDER},
        },
        "summary": {
            "countries": len(countries),
            "complete": sum(1 for c in countries if c["complete"]),
            "present_per_input": present_counts,
            "missing_reasons_per_input": reasons,
        },
        "countries": countries,
    }


def _encode(obj):
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def write_build(snapshot_dir, out):
    """Write map_inputs.json and manifest.json into a new directory <out>. Nothing is overwritten."""
    out = Path(out)
    partial = out.with_name(out.name + ".partial")
    if out.exists():
        raise BuildError(f"refusing to write: {out} already exists")
    if partial.exists():
        raise BuildError(f"refusing to write: {partial} was left by an earlier run; inspect it, then remove it by hand")
    table = build_table(snapshot_dir)
    table_bytes = _encode(table)
    manifest = {
        "schema": BUILD_SCHEMA,
        "snapshot_date_utc": table["snapshot"]["date_utc"],
        "snapshot_manifest_sha256": table["snapshot"]["manifest_sha256"],
        "outputs": {
            OUTPUT_NAME: {"sha256": _sha256(table_bytes), "bytes": len(table_bytes)},
        },
        "summary": table["summary"],
    }
    manifest_bytes = _encode(manifest)

    created = snap._missing_dirs(out.parent)
    out.parent.mkdir(parents=True, exist_ok=True)
    for new_dir in created:
        snap._flush_dir(new_dir.parent)
    partial.mkdir()
    try:
        snap._write_new(partial / OUTPUT_NAME, table_bytes)
        snap._write_new(partial / MANIFEST_NAME, manifest_bytes)
        snap._flush_dir(partial)
        os.rename(partial, out)
    except BaseException:
        print(f"build did not finish; {partial} is left in place for inspection", file=sys.stderr)
        raise
    snap._flush_dir(out.parent)
    return table, manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the map's per-country inputs from a pinned snapshot, offline.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--snapshot", type=Path, help="a snapshot directory, <root>/<date>")
    source.add_argument("--latest-of", type=Path, help="the newest verified snapshot under this root")
    parser.add_argument("--out", type=Path, required=True, help="a NEW directory to write into")
    args = parser.parse_args(argv)
    try:
        snapshot = args.snapshot
        if snapshot is None:
            snapshot = latest_verified_snapshot(args.latest_of)
            if snapshot is None:
                raise BuildError(f"{args.latest_of}: no verified snapshot under this root")
        table, manifest = write_build(snapshot, args.out)
    except (BuildError, snap.SnapshotError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"built from snapshot {table['snapshot']['date_utc']} ({table['snapshot']['manifest_sha256']})")
    print(f"countries: {table['summary']['countries']}; complete on all five inputs: {table['summary']['complete']}")
    print(f"present per input: {table['summary']['present_per_input']}")
    print(f"map_inputs.json sha256: {manifest['outputs'][OUTPUT_NAME]['sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
