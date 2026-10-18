#!/usr/bin/env python3
"""Reproducible before/after comparison for the map score (Council decision C8, part 2).

"Old" is origin/main's app/api/map_data.py, read with `git show` and loaded in
memory. It scores from the live cache when that cache holds a fresh value and from
BENCHMARKS or the hand-entered COUNTRIES profile otherwise.

"New" is this working tree's app/api/map_data.py. It scores from the pinned table
in data/map_inputs (verified against its manifest), with each input's age measured
at the date of use. The live cache and BENCHMARKS are not read.

Offline by construction: nothing is fetched. The old code is compared with an empty
live cache, which is its offline state. The live cache is then filled with
synthetic values written here to show the new score does not move with it. Nothing
here is a measurement of production.

Usage (from the repository root):
    python3 scripts/map_score_c8_comparison.py > scripts/map_score_c8_report.txt
"""
from __future__ import annotations

import os
import statistics
import subprocess
import sys
import types
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault("SECRET_KEY", "ci-test-secret")
os.environ.setdefault("SORA_OFFLINE", "1")
os.environ.setdefault("RUN_SCHEDULER", "false")

import app.external_data as ed  # noqa: E402
from app import map_inputs  # noqa: E402
from app.api import map_data as new  # noqa: E402
from app.country_benchmarks import BENCHMARKS  # noqa: E402

# The date the new score's ages are measured at. Fixed here so the report is reproducible.
AS_OF = date(2026, 10, 19)

OLD_DEFAULTS = {"co2_per_capita": 5.0, "renewable_share": 20.0, "gini_index": 35.0,
                "gov_effectiveness": 0.0, "hdi": 0.7}
INPUT_SHORT = {"renewable_share": "ren", "co2_per_capita": "co2", "gini_index": "gini",
               "gov_effectiveness": "gov", "hdi": "hdi"}


def load_old():
    """origin/main's map_data.py, as it was before this change."""
    source = subprocess.run(
        ["git", "show", "origin/main:app/api/map_data.py"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    module = types.ModuleType("old_map_data")
    module.__file__ = "origin/main:app/api/map_data.py"
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


old = load_old()


def clear_cache():
    ed._live_cache.clear()
    ed._cache_timestamps.clear()


def put_live(name, *, age_hours, indicators, sources):
    ed._live_cache[name] = {"country": name, "indicators": indicators,
                            "indicator_sources": sources, "indicator_periods": {}}
    ed._cache_timestamps[name] = datetime.now() - timedelta(hours=age_hours)


def synthetic_fresh_cache():
    """Every BENCHMARKS country, fresh, with its own values as the 'live' figures."""
    clear_cache()
    for name, row in BENCHMARKS.items():
        if name in ("United States", "United Kingdom"):
            continue  # aliases of USA and UK: the lookup uses the short key, so that is the one to fill
        put_live(name, age_hours=0,
                 indicators={k: row[k] for k in ("renewable_share", "gini_index", "co2_per_capita",
                                                 "gov_effectiveness") if row.get(k) is not None},
                 sources={k: "world_bank" for k in ("renewable_share", "gini_index", "co2_per_capita",
                                                    "gov_effectiveness") if row.get(k) is not None})


def synthetic_stale_cache():
    clear_cache()
    for name, row in BENCHMARKS.items():
        if name in ("United States", "United Kingdom"):
            continue
        put_live(name, age_hours=48,
                 indicators={"renewable_share": row["renewable_share"]},
                 sources={"renewable_share": "world_bank"})


def old_defaults_used(c):
    """The inputs the old code replaced with a constant: absent from both the live entry and BENCHMARKS."""
    d = ed.get_merged_country_data(old._NAME_MAP.get(c["name"], c["name"])) or {}
    ind = (d.get("live") or {}).get("indicators") or {}
    used = []
    for key in ("co2_per_capita", "renewable_share", "gini_index", "gov_effectiveness", "hdi"):
        present = ind.get(key) is not None or d.get(key) is not None
        if not present:
            used.append(f"{key}={OLD_DEFAULTS[key]}")
    return used


def old_score(c):
    score, _extra, source = old._live_esg(c["name"], c["esg"])
    return score, source


def new_score(c):
    return new.score_country(c["code"], as_of=AS_OF)


def years_of(result):
    """The year of each input, in the order the formula reads them; '-' where it has none."""
    parts = []
    for key in new.REQUIRED_INPUTS:
        entry = result["inputs"].get(key)
        parts.append(f"{INPUT_SHORT[key]} {entry['year']}" if entry else f"{INPUT_SHORT[key]} -")
    return ", ".join(parts)


def missing_with_reasons(result):
    return "; ".join(f"{k}: {result['input_notes'][k]}" for k in result["missing_inputs"]) or "-"


def fmt(v):
    return "null" if v is None else str(v)


def comparison_rows():
    clear_cache()
    rows = []
    for c in new.COUNTRIES:
        o_score, o_source = old_score(c)
        n = new_score(c)
        rows.append({
            "code": c["code"], "name": c["name"],
            "old": o_score, "old_label": o_source, "old_defaults": old_defaults_used(c),
            "new": n["esg"], "status": n["esg_status"], "result": n,
        })
    return rows


def median_and_max(values):
    if not values:
        return None, None
    return statistics.median(values), max(values)


def scenario_rows(populate):
    populate()
    labels = {}
    scores = {}
    for c in new.COUNTRIES:
        _, o_source = old_score(c)
        labels[o_source] = labels.get(o_source, 0) + 1
        scores[c["code"]] = new_score(c)["esg"]
    clear_cache()
    return labels, scores


def main():
    rows = comparison_rows()
    print("# C8 part 2: map score, origin/main against this branch (offline, AS_OF %s)\n" % AS_OF.isoformat())
    print("Old = origin/main app/api/map_data.py, empty live cache (its offline state).")
    print("New = this branch: pinned table data/map_inputs/2026-10-18, ages at the date above.")
    print("Each row: old score and label, then the new score or null, status, missing inputs with reasons,")
    print("and the year of each input the new score used (ren = renewables share, co2 = CO2 per capita,")
    print("gini, gov = government effectiveness, hdi).\n")

    print("| code | name | old | old label | new | new status | missing inputs (reason) | input years |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        n = r["result"]
        print(f"| {r['code']} | {r['name']} | {fmt(r['old'])} | {r['old_label']} | {fmt(r['new'])} | "
              f"{r['status']} | {missing_with_reasons(n)} | {years_of(n)} |")

    ok = [r for r in rows if r["status"] == "ok"]
    incomplete = [r for r in rows if r["status"] == "incomplete"]
    both = [r for r in rows if r["new"] is not None]
    changed = [r for r in both if r["new"] != r["old"]]
    removed = [r for r in rows if r["new"] is None]
    deltas = [abs(r["new"] - r["old"]) for r in changed]
    median_delta, max_delta = median_and_max(deltas)
    median_all, max_all = median_and_max([abs(r["new"] - r["old"]) for r in both])

    print("\n## Totals\n")
    print(f"- countries: {len(rows)}")
    print(f"- new status: ok {len(ok)}, incomplete {len(incomplete)}, mixed 0 (no longer produced)")
    print(f"- scores on both sides: {len(both)}")
    print(f"- scores that changed value: {len(changed)}")
    if deltas:
        print(f"  - absolute change, median {median_delta:.1f}, max {max_delta}")
    print(f"- old scores that became null: {len(removed)}")
    print(f"  - {', '.join(r['code'] for r in removed) or 'none'}")
    print(f"- all scored countries, absolute change, median {median_all:.1f}, max {max_all}")
    print(f"- countries the old code scored from a default constant: "
          f"{sum(1 for r in rows if r['old_defaults'])}")
    print(f"- by old label: " + ", ".join(
        f"{label} {sum(1 for r in rows if r['old_label'] == label)}"
        for label in sorted({r['old_label'] for r in rows})))

    print("\n## Korea\n")
    kor = next(r for r in rows if r["code"] == "KOR")
    print(f"- old: {fmt(kor['old'])} ({kor['old_label']}), from the hand-entered COUNTRIES profile "
          f"(the lookup under 'Korea' found no BENCHMARKS row).")
    print(f"- new: {fmt(kor['new'])} ({kor['status']}), from the pinned table, KOR by ISO3: "
          f"{years_of(kor['result'])}.")
    print(f"- change: {fmt(kor['new'] - kor['old']) if kor['new'] is not None else 'null'}. "
          f"This is the old profile replaced by a computed score, not an improvement in the formula.")

    print("\n## Live cache is not an input\n")
    print("The same new scores under three live-cache states. The old code's labels move with the cache; the new must not.\n")
    print("| scenario | old source labels | new scores identical to empty cache |")
    print("|---|---|---|")
    baseline = {r["code"]: r["new"] for r in rows}
    for label, populate in (
        ("empty cache", clear_cache),
        ("stale cache (48h, renewables only)", synthetic_stale_cache),
        ("fresh cache (synthetic, every BENCHMARKS country)", synthetic_fresh_cache),
    ):
        old_labels, new_scores = scenario_rows(populate)
        same = all(new_scores[code] == baseline[code] for code in baseline)
        print(f"| {label} | {old_labels} | {same} |")
    clear_cache()


if __name__ == "__main__":
    main()
