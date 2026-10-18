#!/usr/bin/env python3
"""Reproducible before/after comparison for the map score (Council decision C8).

"Old" is origin/main's app/api/map_data.py, read with `git show` and loaded
from memory; "new" is the working tree's app/api/map_data.py. Both read the same
data layer (app.external_data), so the only difference is the scoring code.

Offline by construction: no request is made. The live cache is either empty or
filled with synthetic values written in this script. Nothing here is a
measurement of production.

Usage (from the repository root):
    python3 scripts/map_score_c8_comparison.py > map_score_comparison.md

Prints three markdown sections: the empty-cache comparison per country (the
static_benchmark and hardcoded paths), totals, and the synthetic live-cache
scenarios (empty, stale, fresh).
"""
from __future__ import annotations

import os
import subprocess
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault("SECRET_KEY", "ci-test-secret")
os.environ.setdefault("SORA_OFFLINE", "1")
os.environ.setdefault("RUN_SCHEDULER", "false")

import app.external_data as ed  # noqa: E402
from app.api import map_data as new  # noqa: E402
from app.country_benchmarks import BENCHMARKS  # noqa: E402

OLD_DEFAULTS = {"co2_per_capita": 5.0, "renewable_share": 20.0, "gini_index": 35.0,
                "gov_effectiveness": 0.0, "hdi": 0.7}


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
    """Every BENCHMARKS country, fresh. Renewables and gini are tagged world_bank; co2 and gov benchmark."""
    clear_cache()
    for name, row in BENCHMARKS.items():
        if name in ("United States", "United Kingdom"):
            continue  # aliases of USA and UK: the lookup uses the short key, so that is the one to fill
        put_live(name, age_hours=0,
                 indicators={"renewable_share": row["renewable_share"], "gini_index": row["gini_index"]}
                 | ({"co2_per_capita": row["co2_per_capita"]} if row.get("co2_per_capita") is not None else {})
                 | ({"gov_effectiveness": row["gov_effectiveness"]} if row.get("gov_effectiveness") is not None else {}),
                 sources={"renewable_share": "world_bank", "gini_index": "world_bank",
                          "co2_per_capita": "benchmark", "gov_effectiveness": "benchmark"})


def synthetic_stale_cache():
    clear_cache()
    for name, row in BENCHMARKS.items():
        if name in ("United States", "United Kingdom"):
            continue
        put_live(name, age_hours=48,
                 indicators={"renewable_share": row["renewable_share"]},
                 sources={"renewable_share": "world_bank"})


def old_defaults_used(name):
    """The inputs the old code replaced with a constant: absent from both the live entry and BENCHMARKS."""
    d = ed.get_merged_country_data(old_lookup(name)) or {}
    ind = (d.get("live") or {}).get("indicators") or {}
    used = []
    for key in ("co2_per_capita", "renewable_share", "gini_index", "gov_effectiveness", "hdi"):
        present = ind.get(key) is not None or d.get(key) is not None
        if not present:
            used.append(f"{key}={OLD_DEFAULTS[key]}")
    return used


def old_lookup(name):
    return old._NAME_MAP.get(name, name)


def old_score(c):
    score, extra, source = old._live_esg(c["name"], c["esg"])
    return score, source


def new_score(c):
    return new.score_country(c["name"])


def reason_for(c, old_s, new_s, defaults):
    parts = []
    if new_s["esg"] is None:
        notes = "; ".join(f"{k}: {v}" for k, v in new_s["input_notes"].items())
        parts.append(f"incomplete ({notes})")
    elif new_s["esg"] != old_s[0]:
        parts.append(f"formula on {new_s['esg_status']} inputs")
    if defaults:
        parts.append("old code used defaults for " + ", ".join(defaults))
    if not parts:
        parts.append("unchanged")
    return "; ".join(parts)


def empty_cache_rows():
    clear_cache()
    rows = []
    for c in new.COUNTRIES:
        o_score, o_source = old_score(c)
        defaults = old_defaults_used(c["name"])
        n = new_score(c)
        rows.append({
            "code": c["code"], "name": c["name"],
            "path": o_source,
            "old": o_score, "new": n["esg"], "status": n["esg_status"],
            "missing": n["missing_inputs"],
            "reason": reason_for(c, (o_score,), n, defaults),
            "changed": n["esg"] != o_score,
        })
    return rows


def sensitivity_rows():
    """NOT the contract: what the scores would be if an unsourced static value were admitted.

    Shows how much of the result rests on the gini and gov_effectiveness sourcing rule.
    """
    clear_cache()
    original = new._static_provenance

    def admit_any(country, key):
        return original(country, key) or {"source": "unsourced", "series": None, "period": None}

    new._static_provenance = admit_any
    try:
        scored = [(c["code"], new.score_country(c["name"])) for c in new.COUNTRIES]
    finally:
        new._static_provenance = original
    return scored


def fmt(v):
    return "null" if v is None else str(v)


def scenario_summary(label, populate):
    populate()
    rows = []
    for c in new.COUNTRIES:
        o_score, o_source = old_score(c)
        n = new_score(c)
        rows.append((o_source, o_score, n["esg_status"], n["esg"]))
    clear_cache()
    old_labels = {}
    for o_source, _, _, _ in rows:
        old_labels[o_source] = old_labels.get(o_source, 0) + 1
    new_status = {}
    for _, _, status, _ in rows:
        new_status[status] = new_status.get(status, 0) + 1
    changed = sum(1 for _, o, _, n in rows if n != o)
    return label, old_labels, new_status, changed, len(rows)


def main():
    print("## Part 2. Offline comparison, empty live cache\n")
    print("Old = origin/main `app/api/map_data.py`. New = this branch. Empty cache, so every "
          "country is on the `static_benchmark` path (in BENCHMARKS) or the `hardcoded` path (not "
          "in BENCHMARKS). Synthetic inputs: none; the data is what the repository holds.\n")
    rows = empty_cache_rows()
    print("| code | name | old path | old score | new score | new status | missing inputs | reason |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        missing = ", ".join(r["missing"]) if r["missing"] else "-"
        print(f"| {r['code']} | {r['name']} | {r['path']} | {r['old']} | {fmt(r['new'])} | "
              f"{r['status']} | {missing} | {r['reason']} |")

    total = len(rows)
    by_status = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    changed = sum(1 for r in rows if r["changed"])
    removed = sum(1 for r in rows if r["new"] is None and r["old"] is not None)
    print("\n### Totals (empty cache)\n")
    print(f"- countries: {total}")
    print(f"- new status: ok {by_status.get('ok', 0)}, mixed {by_status.get('mixed', 0)}, "
          f"incomplete {by_status.get('incomplete', 0)}")
    print(f"- countries whose score changes (number changed, or removed): {changed}")
    print(f"- of those, scores removed to null: {removed}")
    print(f"- countries the old code scored from a default constant: "
          f"{sum(1 for c in new.COUNTRIES if old_defaults_used(c['name']))}")

    print("\n### Sensitivity, not the contract\n")
    print("What the scores would be if a static value with no stated source were admitted "
          "(gini and gov_effectiveness on most countries). This is a question for the owner, not a "
          "result of the contract.\n")
    admitted = sensitivity_rows()
    scored = [(code, s) for code, s in admitted if s["esg"] is not None]
    print(f"- countries that would score: {len(scored)} of {len(admitted)}")
    for code, s in scored:
        print(f"  - {code}: {s['esg']} ({s['esg_status']}, source {s['esg_source']})")

    print("\n## Part 3. Synthetic live-cache scenarios\n")
    print("These are synthetic scenarios written in this script. They are not bounds on real "
          "changes: a real cache can also hold stale, partial or substituted values.\n")
    print("| scenario | old source labels | new statuses | scores that change | countries |")
    print("|---|---|---|---|---|")
    for label, populate in (
        ("empty cache", clear_cache),
        ("stale cache (48h, renewables only)", synthetic_stale_cache),
        ("full fresh cache (synthetic: renewables and gini world_bank, co2 and gov benchmark)",
         synthetic_fresh_cache),
    ):
        name, old_labels, new_status, changed_n, total_n = scenario_summary(label, populate)
        print(f"| {name} | {old_labels} | {new_status} | {changed_n} | {total_n} |")
    clear_cache()


if __name__ == "__main__":
    main()
