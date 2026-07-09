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
  * success           <- 1 if disbursement_percentage>50 OR (Closed AND closing
                         date in the past AND budget>0); 0 if Active/Dropped/
                         Pipeline/future-close. Rows are sampled spread across the
                         whole portfolio (not newest-first) so both completed and
                         in-progress projects appear -> realistic ~50/50 balance.
  * duration_months   <- (closingdate - boardapprovaldate) in months; budget-based
                         proxy fallback when a date is missing.
  * region            <- regionname mapped to AFR/APAC/EU/LATAM/NAM (else Unknown).
  * category          <- sector / project_name keyword-mapped to the existing
                         vocabulary (water/edu/agro/waste/energy); default energy
                         since we filter fq=sector_exact:(Environment).
  * social_impact     <- synthesized normalized 0..100 proxy from log(budget)
                         (WB has no social-impact metric); min-max scaled to 40..95.
"""
import argparse
import csv
import datetime as _dt
import hashlib
import math
import os
import sys
import time

import requests

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

API_URL = "https://search.worldbank.org/api/v2/projects"
FL = ("id,project_name,totalamt,status,sector,regionname,co2_emissions_reduced,url,"
      "boardapprovaldate,closingdate,disbursement_percentage")
FQ = "sector_exact:(Environment)"
PAGE_ROWS = 500

_TODAY = _dt.datetime.now(_dt.timezone.utc)


def parse_wb_date(s):
    """WB returns two date formats: ISO (2024-12-20T00:00:00Z) and
    US (12/20/2025 12:00:00 AM). Return a tz-aware datetime or None."""
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return _dt.datetime.strptime(s, fmt).replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            continue
    return None


def parse_disbursement(row):
    """disbursement_percentage as a float, or None if absent (the v2 API
    currently returns None for this field — kept for forward-compatibility)."""
    v = row.get("disbursement_percentage")
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace("%", "").strip())
    except (ValueError, TypeError):
        return None

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


def map_success(status, closingdate, budget, disb_pct) -> int:
    """Realistic completion signal:
      1  if disbursement_percentage > 50, OR the project has Closed with a
         closing date in the past and a real budget (i.e. it ran to completion).
      0  if it is still Active (not yet finished), Dropped/cancelled, Pipeline,
         or closed in the future / with no budget.
    """
    s = (status or "").strip().lower()
    if any(x in s for x in ("drop", "cancel", "disband")):
        return 0
    if disb_pct is not None and disb_pct > 50:
        return 1
    if "closed" in s and closingdate is not None and closingdate < _TODAY and budget > 0:
        return 1
    return 0


def duration_from_dates(board, closing, budget: float, pid: str) -> int:
    """duration_months = (closingdate - boardapprovaldate) in whole months.
    Falls back to a budget-based proxy when either date is missing/invalid."""
    if board and closing and closing > board:
        months = (closing.year - board.year) * 12 + (closing.month - board.month)
        return int(min(120, max(1, months)))
    return synth_duration(budget, pid)


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

def _fetch_page(os_offset, session):
    """Fetch one page; returns (rows, total) or (None, total) on failure."""
    params = {"format": "json", "fl": FL, "fq": FQ, "rows": PAGE_ROWS, "os": os_offset}
    for attempt in range(3):
        try:
            resp = session.get(API_URL, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            projects = data.get("projects") or {}
            rows = list(projects.values()) if isinstance(projects, dict) else list(projects)
            return rows, int(data.get("total", 0))
        except (requests.RequestException, ValueError) as e:
            print(f"  ! request failed (os={os_offset}, attempt {attempt+1}/3): {e}", file=sys.stderr)
            time.sleep(2 * (attempt + 1))
    return None, 0


def _spread_offsets(total, n_pages):
    """Evenly spaced offsets across the whole portfolio, so the sample mixes
    recent (Active) and older (Closed/Dropped) projects instead of only the
    newest-first Active projects. Pagination newest-first means os=0 is ~100%
    Active; deeper offsets are ~99% Closed — spreading is what yields balance."""
    last = max(0, total - PAGE_ROWS)
    if last == 0 or n_pages <= 1:
        return [0]
    step = last / (n_pages - 1)
    seen, offsets = set(), []
    for i in range(n_pages):
        off = int(round(i * step))
        if off not in seen:
            seen.add(off)
            offsets.append(off)
    return offsets


def fetch_rows(min_projects: int, session: requests.Session):
    """Collect >= min_projects valid rows, spread across the portfolio history."""
    # Discover the total, then fan out across evenly-spaced offsets. Oversize the
    # page count a bit since ~10-40% of rows have no budget and get dropped.
    _, total = _fetch_page(0, session)
    print(f"World Bank reports total={total} Environment projects")
    if not total:
        return []
    n_pages = max(6, math.ceil(min_projects / (PAGE_ROWS * 0.6)))
    offsets = _spread_offsets(total, n_pages)

    collected, fetched = [], set()

    def _consume(os_offset):
        rows, _t = _fetch_page(os_offset, session)
        fetched.add(os_offset)
        if not rows:
            print(f"  os={os_offset}: empty")
            return
        kept = 0
        for row in rows:
            budget = parse_amount(row.get("totalamt"))
            if budget <= 0:
                continue
            collected.append((row, budget))
            kept += 1
        print(f"  os={os_offset}: fetched {len(rows)}, kept {kept} (valid total: {len(collected)})")

    print(f"Spread sampling across {len(offsets)} offsets: {offsets}")
    for off in offsets:
        _consume(off)

    # Top up sequentially (from the newest end) if the spread came up short.
    i = 0
    while len(collected) < min_projects and i * PAGE_ROWS < total:
        off = i * PAGE_ROWS
        i += 1
        if off in fetched:
            continue
        _consume(off)
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
        board = parse_wb_date(row.get("boardapprovaldate"))
        closing = parse_wb_date(row.get("closingdate"))
        disb = parse_disbursement(row)
        records.append({
            "budget": round(budget, 2),
            "co2_reduction": round(parse_co2(row), 1),
            "social_impact": social,
            "duration_months": duration_from_dates(board, closing, budget, pid),
            "category": map_category(row.get("sector"), row.get("project_name")),
            "region": map_region(row.get("regionname")),
            "success": map_success(row.get("status"), closing, budget, disb),
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
