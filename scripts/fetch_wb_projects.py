#!/usr/bin/env python3
"""Fetch World Bank projects (all sectors) and map them into the projects.csv schema.

Pulls real projects from the World Bank projects API, maps their fields onto the
SORA training schema, enriches each with country_gdp_per_capita, and writes a
dataset combining the existing synthetic rows (source="synthetic") with the
fetched World Bank rows (source="worldbank").

By default it pages the ENTIRE portfolio (all ~22729 projects, no sector filter).
Pass --sector Environment to restore the old filter, or --min N to bounded
spread-sample instead of fetching everything.

  * country_gdp_per_capita <- regionname->country->iso->GDP: the project's country
                         (countryshortname via external_data.COUNTRY_ISO3, else the
                         WB countrycode) resolved to an ISO code, then GDP per capita
                         (NY.GDP.PCAP.CD) fetched via app.external_data; missing
                         countries fall back to the dataset median.

Usage:
    python3 scripts/fetch_wb_projects.py [--out data/projects_enriched.csv]

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
                         vocabulary (water/edu/agro/waste/energy); an unmatched
                         row gets "energy" only when --sector actually scoped
                         the fetch to something environment-adjacent, else
                         "Unknown" (see to_records()'s category_default —
                         measured live, 41.1% of an unfiltered fetch matched
                         no rule, and "energy" was not an honest label for
                         "Central Government", "Highways", "Health", ...).
  * social_impact     <- synthesized normalized 0..100 proxy from log(budget)
                         (WB has no social-impact metric); min-max scaled to 40..95.
"""
import argparse
import csv
import datetime as _dt
import hashlib
import json
import math
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dataset_identity import (  # noqa: E402
    MissingSourceProjectId, StageCounts, assert_unique_keys, project_key,
    write_manifest,
)

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)  # so `app.external_data` is importable

API_URL = "https://search.worldbank.org/api/v2/projects"
FL = ("id,project_name,totalamt,status,sector,regionname,countryshortname,countrycode,"
      "co2_emissions_reduced,url,boardapprovaldate,closingdate,disbursement_percentage")
FQ = None  # no sector filter -> all sectors (all ~22729 projects)
PAGE_ROWS = 500
GDP_INDICATOR = "NY.GDP.PCAP.CD"  # World Bank GDP per capita (current US$)

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


def map_category(sector, project_name: str, *, default: str = "energy") -> str:
    """`default` is the caller's decision, not this function's -- see the
    docstring on its only caller's `category_default` parameter for why
    "energy" is wrong for an unfiltered fetch."""
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
    return default


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
    params = {"format": "json", "fl": FL, "rows": PAGE_ROWS, "os": os_offset}
    if FQ:
        params["fq"] = FQ
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


def fetch_all(session: requests.Session):
    """Page through the entire portfolio sequentially (all sectors, all ~22729)."""
    _, total = _fetch_page(0, session)
    print(f"World Bank reports total={total} projects (all sectors)")
    collected, os_off = [], 0
    while total and os_off < total:
        rows, _t = _fetch_page(os_off, session)
        if not rows:
            break
        kept = 0
        for row in rows:
            budget = parse_amount(row.get("totalamt"))
            if budget <= 0:
                continue
            collected.append((row, budget))
            kept += 1
        if os_off % 2500 == 0 or os_off + PAGE_ROWS >= total:
            print(f"  os={os_off}: fetched {len(rows)}, kept {kept} (valid total: {len(collected)})")
        os_off += PAGE_ROWS
    return collected


def resolve_iso(row, country_iso3):
    """regionname->country->iso: prefer the country short-name via external_data's
    COUNTRY_ISO3 (ISO3), else the WB countrycode (ISO2). Both work with the WB
    indicator API. Returns a code string or None."""
    short = (row.get("countryshortname") or "").strip()
    if short in country_iso3:
        return country_iso3[short]
    cc = row.get("countrycode")
    if isinstance(cc, list) and cc:
        return str(cc[0]).strip() or None
    if isinstance(cc, str) and cc.strip():
        return cc.strip()
    return None


def build_gdp_lookup(rows, session):
    """Fetch GDP per capita per unique country code from the WB indicator API
    (reusing app.external_data). Returns (code->gdp dict, median for fallback)."""
    from app.external_data import COUNTRY_ISO3, _fetch_wb_indicator
    codes = {}
    for row in rows:
        code = resolve_iso(row, COUNTRY_ISO3)
        if code:
            codes.setdefault(code, None)
    print(f"Fetching GDP per capita for {len(codes)} unique countries via WB indicator API...")
    found = 0
    for code in list(codes):
        try:
            val = _fetch_wb_indicator(code, GDP_INDICATOR)
        except Exception:
            val = None
        codes[code] = val
        if val is not None:
            found += 1
    vals = sorted(v for v in codes.values() if v is not None)
    median = vals[len(vals) // 2] if vals else 0.0
    print(f"  got GDP for {found}/{len(codes)} countries (median={median:.0f} USD)")
    return codes, median


def to_records(collected, gdp_map, gdp_median, category_default="energy"):
    """Second pass: compute the normalized social_impact score across the batch
    and attach country_gdp_per_capita (region->country->iso->gdp).

    `category_default` is what a row gets when nothing in CATEGORY_RULES
    matches its sector/name text -- "energy" only makes sense when the fetch
    itself was scoped to `fq=sector_exact:(Environment)`, where every row is
    at least environment-adjacent. `fetch_all()`'s default is unfiltered (all
    ~22729 projects, every sector), and measured on a live fetch: 41.1%
    (6652/16188) of rows matched no CATEGORY_RULES bucket and got labelled
    "energy" by the old unconditional default -- among them "Central
    Government", "Highways", "Health", "Railways", "Law and Justice",
    "Banking Institutions". None of those are energy projects; the code
    just had nothing else to say. `main()` passes "Unknown" here (the same
    fallback name `map_region()` already uses) whenever no `--sector` filter
    was applied, so an unmatched row says so rather than claiming a category
    it was never shown evidence for.
    """
    if not collected:
        return []
    from app.external_data import COUNTRY_ISO3
    log_budgets = [math.log10(b) for _, b in collected]
    lo, hi = min(log_budgets), max(log_budgets)
    span = (hi - lo) or 1.0

    records = []
    skipped_no_id = 0
    for (row, budget), lb in zip(collected, log_budgets):
        pid = str(row.get("id") or "").strip()
        # No source_project_id, no row. A row that cannot be deduplicated
        # against a future fetch is exactly the defect this schema closes;
        # letting it through with an empty id would reproduce it immediately.
        if not pid:
            skipped_no_id += 1
            continue
        social = round(40.0 + 55.0 * ((lb - lo) / span), 1)  # 40..95 proxy
        board = parse_wb_date(row.get("boardapprovaldate"))
        closing = parse_wb_date(row.get("closingdate"))
        disb = parse_disbursement(row)
        code = resolve_iso(row, COUNTRY_ISO3)
        gdp = gdp_map.get(code)
        records.append({
            "budget": round(budget, 2),
            "co2_reduction": round(parse_co2(row), 1),
            "social_impact": social,
            "duration_months": duration_from_dates(board, closing, budget, pid),
            "category": map_category(row.get("sector"), row.get("project_name"),
                                      default=category_default),
            "region": map_region(row.get("regionname")),
            "country_gdp_per_capita": round(gdp if gdp is not None else gdp_median, 2),
            "success": map_success(row.get("status"), closing, budget, disb),
            "name": (row.get("project_name") or "").strip().replace("\n", " "),
            "source": "worldbank",
            "source_project_id": pid,
        })
    if skipped_no_id:
        print(f"Skipped {skipped_no_id} World Bank row(s) with no project id "
              f"(cannot be deduplicated; not written to output)")
    return records, skipped_no_id


# --- combine + write ------------------------------------------------------

COLUMNS = ["budget", "co2_reduction", "social_impact", "duration_months",
           "category", "region", "country_gdp_per_capita", "success", "name", "source",
           "source_project_id"]


def load_synthetic(path, gdp_median):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            row = {c: r.get(c, "") for c in COLUMNS}
            row["source"] = "synthetic"
            # No generator-issued id was ever recorded for these rows, and
            # none is invented here -- see dataset_identity.synthetic_key.
            # An explicit empty string, not the column simply being absent.
            row["source_project_id"] = ""
            # synthetic rows have no country -> fill GDP with the WB median
            if not row.get("country_gdp_per_capita"):
                row["country_gdp_per_capita"] = round(gdp_median, 2)
            out.append(row)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=0,
                    help="if >0, bounded spread-sample this many rows instead of the full portfolio")
    ap.add_argument("--sector", default=None,
                    help="optional fq sector_exact filter, e.g. 'Environment' (default: all sectors)")
    ap.add_argument("--base", default=os.path.join(ROOT, "data", "projects.csv"),
                    help="existing synthetic dataset to combine with")
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "projects_enriched.csv"))
    ap.add_argument("--no-combine", action="store_true",
                    help="write only World Bank rows (skip the synthetic base)")
    args = ap.parse_args()

    global FQ
    if args.sector:
        FQ = f"sector_exact:({args.sector})"

    session = requests.Session()
    session.headers.update({"User-Agent": "sora-earth-fetch-wb/1.0"})

    if args.min and args.min > 0:
        print(f"Spread-sampling >= {args.min} World Bank projects (sector filter: {FQ or 'none'})...")
        collected = fetch_rows(args.min, session)
    else:
        print(f"Fetching ALL World Bank projects (sector filter: {FQ or 'none'})...")
        collected = fetch_all(session)

    gdp_map, gdp_median = build_gdp_lookup([r for r, _ in collected], session)
    # "energy" only when a sector filter actually scoped the fetch to
    # something environment-adjacent; "Unknown" (map_region()'s own name for
    # the same situation) otherwise. See to_records()'s docstring.
    category_default = "energy" if FQ else "Unknown"
    wb_records, skipped_no_id_total = to_records(
        collected, gdp_map, gdp_median, category_default=category_default)
    print(f"Mapped {len(wb_records)} World Bank rows.")

    # Persist a country -> GDP lookup so serving (make_features_v2) can supply
    # country_gdp_per_capita at inference from the request's country, using the
    # SAME values training saw. Keyed by both ISO code and country short-name.
    from app.external_data import COUNTRY_ISO3 as _CISO
    by_iso = {code: round(v, 2) for code, v in gdp_map.items() if v is not None}
    by_name = {}
    for row, _b in collected:
        nm = (row.get("countryshortname") or "").strip()
        code = resolve_iso(row, _CISO)
        if nm and code in by_iso:
            by_name.setdefault(nm, by_iso[code])
    gdp_json = os.path.join(ROOT, "models", "country_gdp.json")
    with open(gdp_json, "w") as f:
        json.dump({"by_iso": by_iso, "by_name": by_name, "median": round(gdp_median, 2)}, f)
    print(f"Wrote {len(by_name)} country->GDP entries -> {os.path.relpath(gdp_json, ROOT)}")

    rows = []
    if not args.no_combine:
        synth = load_synthetic(args.base, gdp_median)
        print(f"Loaded {len(synth)} synthetic rows from {os.path.relpath(args.base, ROOT)}")
        rows.extend(synth)
    rows.extend(wb_records)

    # Uniqueness is checked over World Bank rows only: synthetic rows all
    # carry the same empty source_project_id by design (no generator-issued
    # id exists to be unique), and project_key() refuses an empty id before
    # this point is ever reached for them.
    wb_keys = [project_key(r["source"], r["source_project_id"]) for r in wb_records]
    duplicate_count = len(wb_keys) - len(set(wb_keys))
    if duplicate_count:
        assert_unique_keys(wb_keys)  # raises, naming the first duplicate

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    # Atomic write: a temp file in the same directory, then one os.replace.
    # A reader that opens args.out mid-write (this script crashing partway
    # through, or a concurrent run) must never see a truncated CSV.
    tmp_out = args.out + ".tmp"
    with open(tmp_out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_out, args.out)

    n_wb_success = sum(r["success"] for r in wb_records)
    print(f"\nWrote {len(rows)} rows -> {os.path.relpath(args.out, ROOT)}")
    print(f"  synthetic: {len(rows) - len(wb_records)}, worldbank: {len(wb_records)}")
    if wb_records:
        print(f"  WB success rate: {n_wb_success}/{len(wb_records)} "
              f"({100*n_wb_success/len(wb_records):.1f}%)")
        from collections import Counter
        print(f"  WB regions:    {dict(Counter(r['region'] for r in wb_records))}")
        print(f"  WB categories: {dict(Counter(r['category'] for r in wb_records))}")
        gdps = [r["country_gdp_per_capita"] for r in wb_records]
        print(f"  GDP/capita: min={min(gdps):.0f} max={max(gdps):.0f} "
              f"mean={sum(gdps)/len(gdps):.0f} (median fill={gdp_median:.0f})")

    manifest_path = args.out + ".manifest.json"
    write_manifest(
        manifest_path,
        source_url="https://search.worldbank.org/api/v2/projects",
        query_params={"sector": FQ or None, "min": args.min or None,
                      "no_combine": args.no_combine},
        fetch_timestamp=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        commit_sha=_git_commit_sha(),
        stage_counts=[
            StageCounts("wb_fetched", len(collected)),
            StageCounts("wb_mapped_with_id", len(wb_records),
                        reason="rows with a non-empty World Bank project id"),
            StageCounts("wb_skipped_no_id", skipped_no_id_total,
                        reason="World Bank row had no project id; not written"),
            StageCounts("synthetic_rows", len(rows) - len(wb_records)),
            StageCounts("total_written", len(rows)),
        ],
        unique_ids=len(set(wb_keys)),
        duplicate_ids=duplicate_count,
        columns=COLUMNS,
        output_path=args.out,
    )
    print(f"Wrote manifest -> {os.path.relpath(manifest_path, ROOT)}")


def _git_commit_sha() -> str:
    """The commit this run's code came from, best-effort.

    A manifest naming "unknown" is honest about a shallow clone or a
    dirty checkout; guessing would not be.
    """
    import subprocess
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT,
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


if __name__ == "__main__":
    main()
