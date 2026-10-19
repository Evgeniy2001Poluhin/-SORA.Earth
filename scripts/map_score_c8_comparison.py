#!/usr/bin/env python3
"""Reproducible before/after comparison for the map score (Council decision C8, parts 2 and 3).

Both sides are read from commits, and nothing from the working tree is imported or used:

- old: app/api/map_data.py at OLD_SHA (default). OLD_SHA was origin/main's head when the
  report was regenerated. It is fixed, so the comparison does not move when main does.
- new: app/api/map_data.py at NEW_SHA (default HEAD).

For each commit, `git archive` writes its `app` tree and, where the commit has one,
`data/map_inputs` into a fresh temporary directory. Each side then runs in its own
interpreter: `sys.executable -I`, with cwd and PYTHONPATH set to that directory and a
minimal environment. The interpreter imports the code from there, and prints one JSON
object on stdout. The parent process imports nothing from app/ at all.

The run refuses (non-zero exit, nothing on stdout) when:
- a revision does not resolve to a commit;
- this script has uncommitted changes, unless --allow-dirty-script is given, in which case
  the report says NOT REPRODUCIBLE;
- an app module is loaded from outside the archive;
- a loaded module's bytes do not hash to the git blob the commit holds for that path;
- the new side's table does not verify against the manifest pinned in app/map_inputs.py.

The working tree is reported in the header and never enters the comparison. The report
file this script writes (scripts/map_score_c8_report.txt) is not counted as a change, so
regenerating it does not make the header say dirty.

Offline by construction: nothing is fetched. The old code is compared with an empty
live cache, which is its offline state. The live cache is then filled with synthetic
values to show the new score does not move with it. Nothing here is a measurement of
production.

Usage (from the repository root):
    <python> scripts/map_score_c8_comparison.py > scripts/map_score_c8_report.txt
    <python> scripts/map_score_c8_comparison.py --old-sha <sha> --new-sha <sha>
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import statistics
import subprocess
import sys
import tarfile
import tempfile
from datetime import date
from pathlib import Path

SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[1]
REPORT_RELPATH = "scripts/map_score_c8_report.txt"

# The app tree and the pinned input table: the only parts of a commit either side reads.
ARCHIVE_PATHS = ("app", "data/map_inputs")

# The date the new score's ages are measured at. Fixed so the report is reproducible.
AS_OF = date(2026, 10, 19)

# The old side: origin/main's head when this report was regenerated. Fixed, so the
# comparison does not move when main does. Update it deliberately, and say so in the commit.
OLD_SHA = "e4009ec8ca8c6b25ab3616983c3736b63a22b594"

INPUT_SHORT = {"renewable_share": "ren", "co2_per_capita": "co2", "gini_index": "gini",
               "gov_effectiveness": "gov", "hdi": "hdi"}

SCENARIO_LABELS = (
    ("empty", "empty cache"),
    ("stale", "stale cache (48h, renewables only)"),
    ("fresh", "fresh cache (synthetic, every BENCHMARKS country)"),
)

# Runs inside each side's interpreter (`python -I -c RUNNER side tmp as_of repo_root`).
# It prints one JSON object on stdout. Anything else it or the code it imports writes goes
# to stderr, so a stray print cannot corrupt the JSON.
RUNNER = r'''
import importlib
import json
import os
import sys
from datetime import date, datetime, timedelta

EXIT_BAD_ARGS = 2
EXIT_PROVENANCE = 3

side, tmp, as_of_text, repo_root = sys.argv[1:5]
if side not in ("old", "new"):
    sys.stderr.write("unknown side\n")
    sys.exit(EXIT_BAD_ARGS)
result_stream = sys.stdout
sys.stdout = sys.stderr

tmp = os.path.realpath(tmp)
repo_root = os.path.realpath(repo_root)
# -I already drops PYTHONPATH and the working directory. The repository root is dropped as
# well, in case it is on the path, and the archive goes first.
sys.path[:] = [p for p in sys.path if p not in ("", ".") and os.path.realpath(p) != repo_root]
sys.path.insert(0, tmp)

AS_OF = date.fromisoformat(as_of_text)
ed = importlib.import_module("app.external_data")
benchmarks = importlib.import_module("app.country_benchmarks").BENCHMARKS
mod = importlib.import_module("app.api.map_data")
map_inputs = importlib.import_module("app.map_inputs") if side == "new" else None


def provenance():
    """App modules loaded: (relative file paths, namespace package names, names from outside)."""
    base = tmp + os.sep
    files, namespaces, outside = {}, [], []
    for name in sorted(sys.modules):
        if name != "app" and not name.startswith("app."):
            continue
        module = sys.modules[name]
        if module is None:
            continue
        filename = getattr(module, "__file__", None)
        if filename is not None:
            real = os.path.realpath(filename)
            if real.startswith(base):
                files[os.path.relpath(real, tmp).replace(os.sep, "/")] = name
            else:
                outside.append(name)
            continue
        entries = list(getattr(module, "__path__", None) or [])
        if entries and all(os.path.realpath(str(e)).startswith(base) for e in entries):
            namespaces.append(name)
        else:
            outside.append(name)
    return files, sorted(set(namespaces)), sorted(set(outside))


def refuse_if_outside(outside):
    if outside:
        sys.stderr.write("refused: app modules loaded from outside the archive: "
                         + ", ".join(outside) + "\n")
        sys.exit(EXIT_PROVENANCE)


refuse_if_outside(provenance()[2])

FRESH_KEYS = ("renewable_share", "gini_index", "co2_per_capita", "gov_effectiveness")
ALIASES = ("United States", "United Kingdom")
OLD_DEFAULTS = {"co2_per_capita": 5.0, "renewable_share": 20.0, "gini_index": 35.0,
                "gov_effectiveness": 0.0, "hdi": 0.7}


def clear_cache():
    ed._live_cache.clear()
    ed._cache_timestamps.clear()


def put_live(name, age_hours, indicators, sources):
    ed._live_cache[name] = {"country": name, "indicators": indicators,
                            "indicator_sources": sources, "indicator_periods": {}}
    ed._cache_timestamps[name] = datetime.now() - timedelta(hours=age_hours)


def fresh_cache():
    """Every BENCHMARKS country, fresh, with its own values as the 'live' figures."""
    clear_cache()
    for name, row in benchmarks.items():
        if name in ALIASES:
            continue
        put_live(name, 0,
                 {k: row[k] for k in FRESH_KEYS if row.get(k) is not None},
                 {k: "world_bank" for k in FRESH_KEYS if row.get(k) is not None})


def stale_cache():
    clear_cache()
    for name, row in benchmarks.items():
        if name in ALIASES:
            continue
        put_live(name, 48, {"renewable_share": row["renewable_share"]},
                 {"renewable_share": "world_bank"})


SCENARIOS = (("empty", clear_cache), ("stale", stale_cache), ("fresh", fresh_cache))


def old_defaults_used(c):
    """The inputs the old code replaced with a constant: absent from both the live entry and BENCHMARKS."""
    d = ed.get_merged_country_data(mod._NAME_MAP.get(c["name"], c["name"])) or {}
    ind = (d.get("live") or {}).get("indicators") or {}
    used = []
    for key in ("co2_per_capita", "renewable_share", "gini_index", "gov_effectiveness", "hdi"):
        if ind.get(key) is None and d.get(key) is None:
            used.append(f"{key}={OLD_DEFAULTS[key]}")
    return used


def run_old():
    clear_cache()
    countries = []
    for c in mod.COUNTRIES:
        score, _extra, label = mod._live_esg(c["name"], c["esg"])
        countries.append({"code": c["code"], "name": c["name"], "old": score,
                          "old_label": label, "old_defaults": old_defaults_used(c)})
    scenarios = {}
    for key, populate in SCENARIOS:
        populate()
        labels = {}
        for c in mod.COUNTRIES:
            _score, _extra, label = mod._live_esg(c["name"], c["esg"])
            labels[label] = labels.get(label, 0) + 1
        scenarios[key] = labels
    clear_cache()
    return {"countries": countries, "scenarios": scenarios}


def run_new():
    clear_cache()
    state = map_inputs.current_table_state()
    table = state.table
    countries = []
    for c in mod.COUNTRIES:
        n = mod.score_country(c["code"], as_of=AS_OF)
        countries.append({
            "code": c["code"], "name": c["name"], "esg": n["esg"], "esg_status": n["esg_status"],
            "missing_inputs": n["missing_inputs"], "input_notes": n["input_notes"],
            "years": {k: (n["inputs"][k]["year"] if k in n["inputs"] else None)
                      for k in mod.REQUIRED_INPUTS},
        })
    expiry = map_inputs.earliest_expiry(table) if table is not None else None
    on_expiry = []
    if expiry is not None:
        for c in mod.COUNTRIES:
            r = mod.score_country(c["code"], as_of=expiry)
            on_expiry.append({
                "code": c["code"], "esg_status": r["esg_status"],
                "too_old": sorted(k for k, v in r["input_reasons"].items() if v["code"] == "too_old"),
            })
    scenarios = {}
    for key, populate in SCENARIOS:
        populate()
        scenarios[key] = {c["code"]: mod.score_country(c["code"], as_of=AS_OF)["esg"]
                          for c in mod.COUNTRIES}
    clear_cache()
    table_info = {
        "available": table is not None,
        "reason": state.reason,
        "snapshot_date": table.snapshot_date if table is not None else None,
        "earliest_expiry": expiry.isoformat() if expiry is not None else None,
        "table_dir": os.path.relpath(os.path.realpath(str(map_inputs.TABLE_DIR)), tmp).replace(os.sep, "/"),
        "table_sha256": table.table_sha256 if table is not None else None,
        "manifest_sha256": table.manifest_sha256 if table is not None else None,
        "declared_manifest_sha256": map_inputs.PINNED_MANIFEST_SHA256,
    }
    return {"countries": countries, "scenarios": scenarios, "on_expiry": on_expiry,
            "required_inputs": list(mod.REQUIRED_INPUTS), "table": table_info}


body = run_old() if side == "old" else run_new()
files, namespaces, outside = provenance()
refuse_if_outside(outside)
payload = {
    "side": side,
    "python": ".".join(str(v) for v in sys.version_info[:3]),
    "modules": sorted(files),
    "namespace_packages": namespaces,
}
payload.update(body)
result_stream.write(json.dumps(payload) + "\n")
result_stream.flush()
'''


class Refusal(Exception):
    """A reason to stop before printing anything. The message goes to stderr."""


def git(*args):
    proc = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        raise Refusal(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def object_id(spec):
    """The object name of `<commit>:<path>` (a blob for a file, a tree for a directory), or None."""
    proc = subprocess.run(["git", "rev-parse", "--verify", "-q", spec], cwd=ROOT,
                          capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else None


def resolve_commit(rev):
    proc = subprocess.run(["git", "rev-parse", "--verify", "-q", f"{rev}^{{commit}}"], cwd=ROOT,
                          capture_output=True, text=True)
    sha = proc.stdout.strip()
    if proc.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise Refusal(f"{rev!r} does not resolve to a commit")
    return sha


def git_blob_sha(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def working_tree_clean():
    """Nothing is changed except the report this script writes."""
    return git("status", "--porcelain", "--", f":(exclude){REPORT_RELPATH}") == ""


def script_has_uncommitted_changes():
    rel = SCRIPT.relative_to(ROOT).as_posix()
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "--", rel], cwd=ROOT,
                             capture_output=True).returncode == 0
    return (not tracked) or git("status", "--porcelain", "--", rel) != ""


def materialise(sha, paths, dest):
    proc = subprocess.run(["git", "archive", "--format=tar", sha, *paths], cwd=ROOT,
                          capture_output=True)
    if proc.returncode != 0:
        raise Refusal(f"git archive {sha} failed: {proc.stderr.decode(errors='replace').strip()}")
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extractall(dest, filter="data")
        else:
            tar.extractall(dest)


def run_interpreter(side, tmp):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": str(tmp),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "SORA_OFFLINE": "1",
        "RUN_SCHEDULER": "false",
        "REDIS_URL": "",
        "SECRET_KEY": "ci-test-secret",
        "DATABASE_URL": "sqlite:///" + str(tmp / "side.db"),
    }
    proc = subprocess.run(
        [sys.executable, "-I", "-c", RUNNER, side, str(tmp), AS_OF.isoformat(), str(ROOT)],
        cwd=str(tmp), env=env, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-20:])
        raise Refusal(f"the {side} side did not run (exit {proc.returncode}):\n{tail}")
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise Refusal(f"the {side} side printed no JSON ({exc})")


def table_digests(tmp, payload):
    """The pinned table and manifest, hashed from the archived bytes, and the declared pin."""
    info = payload["table"]
    if not info["available"]:
        raise Refusal(f"the new side's pinned table did not verify: {info['reason']}")
    rel_dir = info["table_dir"]
    if not re.fullmatch(r"data/map_inputs/[^/]+", rel_dir):
        raise Refusal(f"unexpected table directory {rel_dir!r}")
    table_sha = sha256_hex((tmp / rel_dir / "map_inputs.json").read_bytes())
    manifest_sha = sha256_hex((tmp / rel_dir / "manifest.json").read_bytes())
    if table_sha != info["table_sha256"] or manifest_sha != info["manifest_sha256"]:
        raise Refusal("the table the new side read differs from the archived bytes")
    source = (tmp / "app" / "map_inputs.py").read_text(encoding="utf-8")
    declared = re.findall(r'^PINNED_MANIFEST_SHA256 = "([0-9a-f]{64})"$', source, flags=re.MULTILINE)
    if len(declared) != 1 or declared[0] != manifest_sha or info["declared_manifest_sha256"] != manifest_sha:
        raise Refusal("manifest.json is not the manifest declared as PINNED_MANIFEST_SHA256 in app/map_inputs.py")
    return {"dir": rel_dir, "table_sha256": table_sha, "manifest_sha256": manifest_sha,
            "declared": declared[0]}


def run_side(side, sha):
    trees = {path: object_id(f"{sha}:{path}") for path in ARCHIVE_PATHS}
    if trees["app"] is None:
        raise Refusal(f"{sha} has no app tree")
    present = [path for path in ARCHIVE_PATHS if trees[path] is not None]
    with tempfile.TemporaryDirectory(prefix=f"map-c8-{side}-") as name:
        tmp = Path(name)
        materialise(sha, present, tmp)
        payload = run_interpreter(side, tmp)
        modules = []
        for rel in payload["modules"]:
            expected = object_id(f"{sha}:{rel}")
            path = tmp / rel
            if expected is None or not path.is_file() or git_blob_sha(path.read_bytes()) != expected:
                raise Refusal(f"the {side} side loaded {rel}, whose bytes are not the blob {sha} holds for it")
            modules.append((rel, expected))
        digests = table_digests(tmp, payload) if side == "new" else None
    return {"sha": sha, "trees": trees, "modules": modules,
            "namespace_packages": payload["namespace_packages"], "python": payload["python"],
            "payload": payload, "digests": digests}


def fmt(v):
    return "null" if v is None else str(v)


def median_and_max(values):
    if not values:
        return None, None
    return statistics.median(values), max(values)


def missing_with_reasons(r):
    return "; ".join(f"{k}: {r['notes'][k]}" for k in r["missing"]) or "-"


def years_of(r, required):
    parts = []
    for key in required:
        year = r["years"][key]
        parts.append(f"{INPUT_SHORT[key]} {year}" if year else f"{INPUT_SHORT[key]} -")
    return ", ".join(parts)


def provenance_lines(old, new, meta):
    lines = ["## Provenance", ""]
    lines.append(f"- old commit: {old['sha']}")
    lines.append(f"- new commit: {new['sha']}")
    lines.append(f"- AS_OF: {AS_OF.isoformat()}")
    if old["python"] == new["python"]:
        lines.append(f"- python: {old['python']} (both sides, each in its own interpreter, -I)")
    else:
        lines.append(f"- python: old {old['python']}, new {new['python']} (each in its own interpreter, -I)")
    lines.append(f"- working tree: {meta['tree']}")
    if meta["not_reproducible"]:
        lines.append("NOT REPRODUCIBLE: comparison script has uncommitted changes")
    lines.append(f"- comparison script: {meta['script_rel']}, sha256 {meta['script_sha256']}")
    lines.append(f"- tree app: old {old['trees']['app']}, new {new['trees']['app']}")
    old_maps = old["trees"]["data/map_inputs"] or "absent at this commit"
    lines.append(f"- tree data/map_inputs: old {old_maps}, new {new['trees']['data/map_inputs']}")
    d = new["digests"]
    lines.append(f"- pinned table, new side: {d['dir']}/map_inputs.json sha256 {d['table_sha256']}")
    lines.append(f"- pinned table, new side: {d['dir']}/manifest.json sha256 {d['manifest_sha256']}")
    match = "equal to the manifest sha256" if d["declared"] == d["manifest_sha256"] else "DIFFERS from the manifest sha256"
    lines.append(f"- PINNED_MANIFEST_SHA256 declared in app/map_inputs.py: {d['declared']}, {match}")
    for label, side in (("old", old), ("new", new)):
        lines.append(f"- app modules loaded by the {label} side (path, git blob):")
        for name in side["namespace_packages"]:
            lines.append(f"  - {name} (namespace package, no file)")
        for rel, blob in side["modules"]:
            lines.append(f"  - {rel} {blob}")
    return lines


def render(old, new, meta):
    old_payload, new_payload = old["payload"], new["payload"]
    old_rows = {c["code"]: c for c in old_payload["countries"]}
    if set(old_rows) != {c["code"] for c in new_payload["countries"]}:
        raise Refusal("the two commits do not list the same countries")
    required = new_payload["required_inputs"]
    rows = []
    for c in new_payload["countries"]:
        o = old_rows[c["code"]]
        rows.append({
            "code": c["code"], "name": c["name"],
            "old": o["old"], "old_label": o["old_label"], "old_defaults": o["old_defaults"],
            "new": c["esg"], "status": c["esg_status"],
            "missing": c["missing_inputs"], "notes": c["input_notes"], "years": c["years"],
        })
    info = new_payload["table"]
    digests = new["digests"]

    ok = [r for r in rows if r["status"] == "ok"]
    incomplete = [r for r in rows if r["status"] == "incomplete"]
    both = [r for r in rows if r["new"] is not None]
    changed = [r for r in both if r["new"] != r["old"]]
    removed = [r for r in rows if r["new"] is None]
    deltas = [abs(r["new"] - r["old"]) for r in changed]
    median_delta, max_delta = median_and_max(deltas)
    median_all, max_all = median_and_max([abs(r["new"] - r["old"]) for r in both])
    if info["earliest_expiry"] is None:
        raise Refusal("the pinned table has no value with a year, so it has no expiry date")
    expiry = date.fromisoformat(info["earliest_expiry"])
    on_expiry = new_payload["on_expiry"]

    out = []
    w = out.append
    w(f"# C8 part 2 and 3: map score, old commit against new commit (offline, AS_OF {AS_OF.isoformat()})")
    w("")
    w("Offline comparison of two commits. Not a production measurement.")
    w("")
    w(f"Old = app/api/map_data.py at {old['sha']} (origin/main when this was generated), empty live cache.")
    w(f"New = app/api/map_data.py at {new['sha']}, pinned table {digests['dir']} "
      f"(manifest sha256 {digests['manifest_sha256'][:16]}...), ages at the date above.")
    w("Each row: old score and label, then the new score or null, status, missing inputs with reasons,")
    w("and the year of each input the new score used (ren = renewables share, co2 = CO2 per capita,")
    w("gini, gov = government effectiveness, hdi).")
    w("")
    out.extend(provenance_lines(old, new, meta))
    w("")
    w("| code | name | old | old label | new | new status | missing inputs (reason) | input years |")
    w("|---|---|---|---|---|---|---|---|")
    for r in rows:
        w(f"| {r['code']} | {r['name']} | {fmt(r['old'])} | {r['old_label']} | {fmt(r['new'])} | "
          f"{r['status']} | {missing_with_reasons(r)} | {years_of(r, required)} |")

    w("")
    w("## Totals")
    w("")
    w(f"- countries: {len(rows)}")
    w(f"- new status: ok {len(ok)}, incomplete {len(incomplete)}, mixed 0 (no longer produced)")
    w(f"- scores on both sides: {len(both)}")
    w(f"- scores that changed value: {len(changed)}")
    if deltas:
        w(f"  - absolute change, median {median_delta:.1f}, max {max_delta}")
    w(f"- old scores that became null: {len(removed)}")
    w(f"  - {', '.join(r['code'] for r in removed) or 'none'}")
    w(f"- all scored countries, absolute change, median {median_all:.1f}, max {max_all}")
    w(f"- countries the old code scored from a default constant: "
      f"{sum(1 for r in rows if r['old_defaults'])}")
    w("- by old label: " + ", ".join(
        f"{label} {sum(1 for r in rows if r['old_label'] == label)}"
        for label in sorted({r['old_label'] for r in rows})))
    w(f"- pinned table: snapshot {info['snapshot_date']}, earliest input expires {expiry.isoformat()}")
    too_old_flags = sum(1 for r in on_expiry if {"renewable_share", "hdi"} & set(r["too_old"]))
    w(f"- on {expiry.isoformat()} (first day too old): "
      f"ok {sum(r['esg_status'] == 'ok' for r in on_expiry)}, "
      f"incomplete {sum(r['esg_status'] == 'incomplete' for r in on_expiry)}, "
      f"countries with a too_old reason for renewables or HDI {too_old_flags}")

    w("")
    w("## Korea")
    w("")
    kor = next(r for r in rows if r["code"] == "KOR")
    w(f"- old: {fmt(kor['old'])} ({kor['old_label']}), from the hand-entered COUNTRIES profile "
      f"(the lookup under 'Korea' found no BENCHMARKS row).")
    w(f"- new: {fmt(kor['new'])} ({kor['status']}), from the pinned table, KOR by ISO3: "
      f"{years_of(kor, required)}.")
    w(f"- change: {fmt(kor['new'] - kor['old']) if kor['new'] is not None else 'null'}. "
      f"This is the old profile replaced by a computed score, not an improvement in the formula.")

    w("")
    w("## Live cache is not an input")
    w("")
    w("The same new scores under three live-cache states. The old code's labels move with the cache; the new must not.")
    w("")
    w("| scenario | old source labels | new scores identical to empty cache |")
    w("|---|---|---|")
    baseline = {r["code"]: r["new"] for r in rows}
    for key, label in SCENARIO_LABELS:
        old_labels = old_payload["scenarios"][key]
        new_scores = new_payload["scenarios"][key]
        same = all(new_scores[code] == baseline[code] for code in baseline)
        w(f"| {label} | {old_labels} | {same} |")
    return "\n".join(out) + "\n"


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Offline before/after comparison of the map score, from two commits.")
    parser.add_argument("--old-sha", default=OLD_SHA,
                        help="revision on the old side (default: %(default)s)")
    parser.add_argument("--new-sha", default="HEAD",
                        help="revision on the new side (default: HEAD)")
    parser.add_argument("--allow-dirty-script", action="store_true",
                        help="run although this script has uncommitted changes; the report is then marked NOT REPRODUCIBLE")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        old_sha = resolve_commit(args.old_sha)
        new_sha = resolve_commit(args.new_sha)
        script_dirty = script_has_uncommitted_changes()
        if script_dirty and not args.allow_dirty_script:
            raise Refusal(
                "scripts/map_score_c8_comparison.py has uncommitted changes, so no commit holds the file "
                "that produced this report. Commit it first, or pass --allow-dirty-script (the report is "
                "then marked NOT REPRODUCIBLE)."
            )
        meta = {
            "script_rel": SCRIPT.relative_to(ROOT).as_posix(),
            "script_sha256": sha256_hex(SCRIPT.read_bytes()),
            "tree": "clean" if working_tree_clean() else "dirty (not used: both sides are read from commits)",
            "not_reproducible": script_dirty,
        }
        old = run_side("old", old_sha)
        new = run_side("new", new_sha)
        report = render(old, new, meta)
    except Refusal as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
