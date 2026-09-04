#!/usr/bin/env python3
"""Measure how much `duration_months` alone predicts `success`, and why.

`fetch_wb_projects.py` computes both columns from the same underlying fact --
whether a project has a usable `closingdate`. `duration_from_dates()` uses a
real `(closing - board)` difference when both dates are present and
`closing > board`; otherwise it falls back to `synth_duration(budget, pid)`,
a deterministic function of `budget` alone. `map_success()` returns 1 mostly
when a project is Closed with a past closing date -- i.e. almost exactly the
condition that decides which duration a row got. `budget` and
`duration_months` are two of the model's seven actually-informative features
(CLAUDE.md's "nine columns, seven of which carry information"), so if the
two columns correlate through this shared cause, a naive rule using only
them can predict `success` without touching anything that looks like ESG
signal.

Two modes, chosen automatically by what the input file carries:

  exact       `source_project_id` is present (this audit's PR #223 schema).
              `synth_duration(budget, pid)` is recomputed row-by-row and
              compared byte-for-byte to the stored `duration_months`.
  approximate `data/projects.csv` has no project id (a separate, earlier
              finding -- see docs/DATASET_AUDIT_2026-09-03.md), so the exact
              hash-jittered value can't be recomputed. A shape-only test is
              used instead: is duration_months within [6,72] and within
              +-6.5 of 6*log10(budget), the formula's own bounds. This is a
              deliberately cruder test and is labelled as such in the output
              -- it is expected to under- or over-count relative to `exact`.

Either way the output is a naive rule -- "if duration looks synthetic,
predict success=0, else 1" -- and its accuracy, which is the number that
matters: how much of `success` two ordinary features can fake.

No network. No write to the input file. Read-only measurement.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from fetch_wb_projects import synth_duration  # noqa: E402


def classify_exact(budget: float, duration: float, source_project_id: str) -> bool:
    """True if `duration` is exactly what synth_duration(budget, pid) gives."""
    return int(round(duration)) == synth_duration(budget, source_project_id)


def classify_approximate(budget: float, duration: float) -> bool:
    """True if `duration` merely has the shape synth_duration() produces.

    Cruder than `classify_exact` on purpose -- see the module docstring. Uses
    the same bounds and jitter range synth_duration() itself declares
    (`[6, 72]`, `base +- 6`), not an independently chosen threshold.
    """
    base = 6 * math.log10(max(budget, 10.0))
    return 6 <= duration <= 72 and abs(duration - base) <= 6.5


def evaluate(rows: list[dict], has_ids: bool) -> dict:
    """rows: dicts with at least budget, duration_months, success (strings ok);
    source_project_id required when has_ids. Returns the measured summary."""
    counts = {"00": 0, "01": 0, "10": 0, "11": 0}  # "{success}{is_synthetic}"
    n = 0
    for r in rows:
        budget = float(r["budget"])
        duration = float(r["duration_months"])
        success = str(r["success"]).strip()
        if success not in ("0", "1"):
            continue
        if has_ids:
            is_synth = classify_exact(budget, duration, r["source_project_id"])
        else:
            is_synth = classify_approximate(budget, duration)
        counts[f"{success}{'1' if is_synth else '0'}"] += 1
        n += 1

    if n == 0:
        return {"rows_evaluated": 0}

    # naive rule: synthetic-shaped duration -> predict success=0, else 1
    correct = counts["01"] + counts["10"]
    return {
        "rows_evaluated": n,
        "mode": "exact" if has_ids else "approximate",
        "success0_synthetic_duration": counts["01"],
        "success0_real_duration": counts["00"],
        "success1_synthetic_duration": counts["11"],
        "success1_real_duration": counts["10"],
        "naive_rule_correct": correct,
        "naive_rule_accuracy": round(correct / n, 4),
    }


def load_rows(path: str) -> tuple[list[dict], bool]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = [r for r in reader if r.get("source") == "worldbank"]
    has_ids = "source_project_id" in fieldnames and any(
        (r.get("source_project_id") or "").strip() for r in rows
    )
    return rows, has_ids


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="data/projects.csv",
                     help="CSV with budget, duration_months, success, source "
                          "columns (optionally source_project_id)")
    ap.add_argument("--json-out", default=None,
                     help="also write the summary as JSON to this path")
    args = ap.parse_args()

    rows, has_ids = load_rows(args.data)
    result = evaluate(rows, has_ids)
    result["data_path"] = args.data
    print(json.dumps(result, indent=2))

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        print(f"\nwrote {args.json_out}", file=sys.stderr)


if __name__ == "__main__":
    main()
