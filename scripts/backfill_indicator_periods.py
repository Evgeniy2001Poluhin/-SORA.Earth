"""Establish, where it can be established, when each indicator value applies.

country_indicator_history holds 90,403 rows with no observation period. The
ingester dropped the year the World Bank stated alongside every figure (#58);
that is fixed for new rows (#59), and these are what was written before.

The three outcomes are recorded rather than collapsed:

  stated           the source's own year, recovered and written to as_of_date
  source_has_none  benchmarks and global averages carry no period, and never did
  unrecoverable    a period existed and cannot now be established

Nothing is guessed. A row whose period cannot be recovered is marked as such,
because a NULL that means "we lost it" and a NULL that means "there was never
one" call for different responses, and until this runs they are the same NULL.

## How a period is recovered

The API returns each indicator as a series of (year, value). A stored figure is
matched against that series: exactly one year with the same value identifies it.

This is weaker than it looks and the weakness is recorded, not hidden:

  * the World Bank revises figures. SAU/NY.GDP.PCAP.CD holds 35121.66, which
    appears against no year the API offers today -- the value is real, its year
    is simply no longer discoverable from the source.
  * one value can occur in several years, and then the match identifies nothing.

Both cases end as `unrecoverable`. That is the honest answer, and pretending
otherwise would put a fabricated date on a real measurement -- worse than the
NULL it replaces, because a date is believed.

## Safety

Idempotent: only rows with period_status IS NULL are considered, so a re-run
resumes and never revisits a verdict. Refused fetches leave their rows untouched
rather than marking them unrecoverable, so a rate limit cannot be mistaken for a
loss -- the next run picks them up.

Dry by default. `--apply` writes.

    python scripts/backfill_indicator_periods.py            # report only
    python scripts/backfill_indicator_periods.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime

import psycopg2

API = "https://api.worldbank.org/v2/country/{iso}/indicator/{ind}"
RUN_TAG = "backfill_indicator_periods"

# The first measurement fired 118 requests without pausing and the API refused
# 56 of them. Counted as failures to match, that read as "40% recoverable" --
# a statement about the rate limiter rather than about the data. Paced now.
DELAY_SECONDS = 1.5
RETRIES = 3
# Enough history to cover anything this table holds; the oldest rows are from
# 2026 and the indicators are annual.
MRV = 40

STATED = "stated"
SOURCE_HAS_NONE = "source_has_none"
UNRECOVERABLE = "unrecoverable"

# Sources that publish no period. Not a guess: app/external_data.py returns None
# for their period explicitly, and the comment there says why.
PERIODLESS_SOURCES = ("benchmark", "global_avg")


def connect():
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set")
    return psycopg2.connect(url.replace("postgresql+psycopg://", "postgresql://"))


def fetch_history(iso: str, ind: str):
    """(year -> value) for one indicator, or None if the source would not say.

    None is distinct from an empty history: one means "ask again later", the
    other means "the source has nothing", and only the second is evidence.
    """
    url = API.format(iso=iso, ind=ind) + f"?format=json&per_page=60&mrv={MRV}"
    for attempt in range(RETRIES):
        try:
            payload = json.loads(urllib.request.urlopen(url, timeout=30).read().decode())
            rows = payload[1] if len(payload) > 1 and payload[1] else []
            return {r["date"]: r["value"] for r in rows if r.get("value") is not None}
        except Exception:
            if attempt == RETRIES - 1:
                return None
            time.sleep(2 ** attempt * 2)
    return None


def match_year(value: float, history: dict) -> str | None:
    """The single year holding this value, or None when that is not one year.

    The tolerance covers the rounding the ingester applied on the way in; it is
    relative, because these indicators span dollars to percentages.
    """
    tolerance = max(abs(value) * 1e-6, 0.005)
    years = [y for y, v in history.items() if abs(v - value) <= tolerance]
    return years[0] if len(years) == 1 else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the results; without it nothing is changed")
    parser.add_argument("--limit-pairs", type=int, default=0,
                        help="stop after this many country/indicator pairs")
    args = parser.parse_args()

    con = connect()
    cur = con.cursor()

    # --- the sources that never had a period -------------------------------
    cur.execute(
        """SELECT count(*) FROM country_indicator_history
           WHERE period_status IS NULL AND source = ANY(%s)""",
        (list(PERIODLESS_SOURCES),),
    )
    periodless = cur.fetchone()[0]

    if args.apply and periodless:
        cur.execute(
            """UPDATE country_indicator_history
               SET period_status = %s, period_source = %s
               WHERE period_status IS NULL AND source = ANY(%s)""",
            (SOURCE_HAS_NONE, RUN_TAG, list(PERIODLESS_SOURCES)),
        )
        con.commit()

    # --- the rows whose period the source stated ---------------------------
    cur.execute(
        """SELECT country_iso3, indicator_code, value, count(*)
           FROM country_indicator_history
           WHERE period_status IS NULL
             AND as_of_date IS NULL
             AND source NOT IN %s
           GROUP BY 1, 2, 3""",
        (PERIODLESS_SOURCES,),
    )
    by_pair: dict[tuple[str, str], list[tuple[float, int]]] = defaultdict(list)
    for iso, ind, value, count in cur.fetchall():
        by_pair[(iso, ind)].append((value, count))

    pairs = sorted(by_pair)
    if args.limit_pairs:
        pairs = pairs[: args.limit_pairs]

    stated = unrecoverable = deferred = 0
    refused_pairs = 0

    for i, key in enumerate(pairs, 1):
        iso, ind = key
        history = fetch_history(iso, ind)
        time.sleep(DELAY_SECONDS)

        if history is None:
            # Untouched on purpose. Marking these unrecoverable would record a
            # rate limit as a permanent loss, and the row would never be looked
            # at again.
            refused_pairs += 1
            deferred += sum(n for _, n in by_pair[key])
            continue

        for value, count in by_pair[key]:
            year = match_year(value, history)
            if year:
                stated += count
                if args.apply:
                    cur.execute(
                        """UPDATE country_indicator_history
                           SET as_of_date = %s, period_status = %s, period_source = %s
                           WHERE period_status IS NULL AND as_of_date IS NULL
                             AND country_iso3 = %s AND indicator_code = %s
                             AND value = %s""",
                        (datetime(int(year), 1, 1), STATED, RUN_TAG, iso, ind, value),
                    )
            else:
                unrecoverable += count
                if args.apply:
                    cur.execute(
                        """UPDATE country_indicator_history
                           SET period_status = %s, period_source = %s
                           WHERE period_status IS NULL AND as_of_date IS NULL
                             AND country_iso3 = %s AND indicator_code = %s
                             AND value = %s""",
                        (UNRECOVERABLE, RUN_TAG, iso, ind, value),
                    )

        if args.apply:
            con.commit()
        if i % 20 == 0:
            print(f"  ... {i}/{len(pairs)} pairs", flush=True)

    con.commit()

    verb = "set" if args.apply else "would set"
    print()
    print(f"  {verb} {SOURCE_HAS_NONE:16} {periodless:>7}  "
          f"(benchmark and global averages, which publish no period)")
    print(f"  {verb} {STATED:16} {stated:>7}  (the source's own year, recovered)")
    print(f"  {verb} {UNRECOVERABLE:16} {unrecoverable:>7}  "
          f"(revised figures, or a value occurring in several years)")
    print(f"  left for a later run   {deferred:>7}  "
          f"({refused_pairs} pairs the API refused; a rate limit is not a loss)")
    if not args.apply:
        print()
        print("  nothing was written. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
