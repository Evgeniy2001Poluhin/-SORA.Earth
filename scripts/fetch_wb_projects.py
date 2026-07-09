#!/usr/bin/env python3
"""Fetch World Bank Environment projects and map them into the projects.csv schema.

Pulls real projects from the World Bank projects API, maps their fields onto the
SORA training schema, and writes an *enriched* dataset that combines the existing
synthetic rows (source="synthetic") with the fetched World Bank rows
(source="worldbank").

Usage:
    python3 scripts/fetch_wb_projects.py [--min 2000] [--out data/projects_enriched.csv]

Then retrain on it:
    python3 scripts/retrain_ensemble_v2.py --data data/projects_enriched.csv

Notes / assumptions (the WB API does not expose every field the model needs):
  * budget            <- totalamt (commas stripped); rows with budget<=0 are dropped.
  * co2_reduction     <- co2_emissions_reduced when present, else 0.
  * success           <- status in {Active, Closed, Completed} -> 1, else 0.
  * region            <- regionname mapped to AFR/APAC/EU/LATAM/NAM (else Unknown).
  * category          <- sector / project_name keyword-mapped to the existing
                         vocabulary (water/edu/agro/waste/energy); default energy
                         since we filter fq=sector_exact:(Environment).
  * duration_months   <- synthesized deterministically from budget (WB gives no
                         dates in the requested field list), clipped to [6, 72].
  * social_impact     <- synthesized normalized 0..100 proxy from log(budget)
                         (WB has no social-impact metric); min-max scaled to 40..95.
"""
import argparse
import csv
import hashlib
import math
import os
import sys
import time

import requests

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

API_URL = "https://search.worldbank.org/api/v2/projects"
FL = "id,project_name,totalamt,status,sector,regionname,co2_emissions_reduced,url"
FQ = "sector_exact:(Environment)"
PAGE_ROWS = 500

# --- field mappings -------------------------------------------------------

REGION_RULES = [
    ("africa", "AFR"),
    ("middle east", "AFR"),          # MENA -> North Africa bucket
    ("east asia", "APAC"),
    ("south asia", "APAC"),
    ("pacific", "APAC"),
    ("europe", "EU"),
    ("central asia", "EU"),
    ("latin america", "LATAM"),
    ("caribbean", "LATAM"),
    ("north america", "NAM"),
]

CATEGORY_RULES = [
    (("water", "sanitation", "irrigation", "wastewater"), "water"),
    (("education", "school", "learning"), "edu"),
    (("agri", "forest", "rural", "land", "fish", "crop"), "agro"),
    (("waste", "pollution", "recycl", "solid waste"), "waste"),
    (("energy", "renewable", "power", "electric", "climate", "green", "environment"), "energy"),
]


def map_region(regionname: str) -> str:
    r = (regionname or "").lower()
    for needle, code in REGION_RULES:
        if needle in r:
            return code
    return "Unknown"


def map_category(sector, project_name: str) -> str:
    # sector may be a string, a list of dicts ({"Name": ...}), or absent.
    parts = []
    if isinstance(sector, list):
        for s in sector:
            if isinstance(s, dict):
                parts.append(str(s.get("Name") or s.get("name") or ""))
            else:
                parts.append(str(s))
    elif sector:
        parts.append(str(sector))
    parts.append(project_name or "")
    text = " ".join(parts).lower()
    for needles, code in CATEGORY_RULES:
        if any(n in text for n in needles):
            return code
    # We filtered on Environment, so the sensible default is energy.
    return "energy"


def map_success(status: str) -> int:
    s = (status or "").strip().lower()
    return 1 if s in ("active", "closed", "completed") else 0


def parse_amount(totalamt) -> float:
    if totalamt is None:
        return 0.0
    try:
        return float(str(totalamt).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def parse_co2(row) -> float:
    v = row.get("co2_emissions_reduced")
    try:
        return float(str(v).replace(",", "").strip()) if v not in (None, "") else 0.0
    except (ValueError, TypeError):
        return 0.0


def synth_duration(budget: float, pid: str) -> int:
    """Deterministic duration proxy: larger budgets run longer. Range [6, 72]."""
    base = 6 * math.log10(max(budget, 10.0))          # ~30 at 1e5, ~50 at 2e8
    jitter = (int(hashlib.md5((pid or "").encode()).hexdigest(), 16) % 13) - 6  # -6..+6
    return int(min(72, max(6, round(base + jitter))))


# --- fetch ----------------------------------------------------------------

def fetch_rows(min_projects: int, session: requests.Session):
    """Page through the API (os += 500) until we have >= min_projects valid rows."""
    collected = []
    os_offset = 0
    total = None
    while True:
        params = {
            "format": "json",
            "fl": FL,
            "fq": FQ,
            "rows": PAGE_ROWS,
            "os": os_offset,
        }
        data = None
        for attempt in range(3):
            try:
                resp = session.get(API_URL, params=params, timeout=60)
                resp.raise_for_status()
                data = resp.json()
                break
            except (requests.RequestException, ValueError) as e:
                print(f"  ! request failed (os={os_offset}, attempt {attempt+1}/3): {e}", file=sys.stderr)
                time.sleep(2 * (attempt + 1))
        if data is None:
            print("  ! giving up on this page after 3 attempts", file=sys.stderr)
            break

        if total is None:
            total = int(data.get("total", 0))
            print(f"World Bank reports total={total} Environment projects")

        projects = data.get("projects") or {}
        page = list(projects.values()) if isinstance(projects, dict) else list(projects)
        if not page:
            print("  (empty page — stopping)")
            break

        kept = 0
        for row in page:
            budget = parse_amount(row.get("totalamt"))
            if budget <= 0:
                continue
            collected.append((row, budget))
            kept += 1

        os_offset += PAGE_ROWS
        print(f"  os={os_offset - PAGE_ROWS}: fetched {len(page)}, kept {kept} "
              f"(valid total: {len(collected)})")

        if len(collected) >= min_projects:
            break
        if total and os_offset >= total:
            break
    return collected


def to_records(collected):
    """Second pass: compute the normalized social_impact score across the batch."""
    if not collected:
        return []
    log_budgets = [math.log10(b) for _, b in collected]
    lo, hi = min(log_budgets), max(log_budgets)
    span = (hi - lo) or 1.0

    records = []
    for (row, budget), lb in zip(collected, log_budgets):
        pid = str(row.get("id") or "")
        social = round(40.0 + 55.0 * ((lb - lo) / span), 1)  # 40..95 proxy
        records.append({
            "budget": round(budget, 2),
            "co2_reduction": round(parse_co2(row), 1),
            "social_impact": social,
            "duration_months": synth_duration(budget, pid),
            "category": map_category(row.get("sector"), row.get("project_name")),
            "region": map_region(row.get("regionname")),
            "success": map_success(row.get("status")),
            "name": (row.get("project_name") or "").strip().replace("\n", " "),
            "source": "worldbank",
        })
    return records


# --- combine + write ------------------------------------------------------

COLUMNS = ["budget", "co2_reduction", "social_impact", "duration_months",
           "category", "region", "success", "name", "source"]


def load_synthetic(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            r.setdefault("source", "synthetic")
            r["source"] = "synthetic"
            out.append({c: r.get(c, "") for c in COLUMNS})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=2000, help="minimum valid WB projects to fetch")
    ap.add_argument("--base", default=os.path.join(ROOT, "data", "projects.csv"),
                    help="existing synthetic dataset to combine with")
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "projects_enriched.csv"))
    ap.add_argument("--no-combine", action="store_true",
                    help="write only World Bank rows (skip the synthetic base)")
    args = ap.parse_args()

    session = requests.Session()
    session.headers.update({"User-Agent": "sora-earth-fetch-wb/1.0"})

    print(f"Fetching >= {args.min} World Bank Environment projects...")
    collected = fetch_rows(args.min, session)
    wb_records = to_records(collected)
    print(f"Mapped {len(wb_records)} World Bank rows.")
    if len(wb_records) < args.min:
        print(f"WARNING: only {len(wb_records)} valid rows (< requested {args.min}).", file=sys.stderr)

    rows = []
    if not args.no_combine:
        synth = load_synthetic(args.base)
        print(f"Loaded {len(synth)} synthetic rows from {os.path.relpath(args.base, ROOT)}")
        rows.extend(synth)
    rows.extend(wb_records)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)

    n_wb_success = sum(r["success"] for r in wb_records)
    print(f"\nWrote {len(rows)} rows -> {os.path.relpath(args.out, ROOT)}")
    print(f"  synthetic: {len(rows) - len(wb_records)}, worldbank: {len(wb_records)}")
    if wb_records:
        print(f"  WB success rate: {n_wb_success}/{len(wb_records)} "
              f"({100*n_wb_success/len(wb_records):.1f}%)")
        from collections import Counter
        print(f"  WB regions:    {dict(Counter(r['region'] for r in wb_records))}")
        print(f"  WB categories: {dict(Counter(r['category'] for r in wb_records))}")


if __name__ == "__main__":
    main()
