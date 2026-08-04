"""Infer, where it can be inferred, when each indicator value applies.

country_indicator_history holds 90,403 rows with no observation period. The
ingester discarded the year the World Bank stated alongside every figure (#58);
that is fixed for new rows (#59), and these are what was written before.

## What this can and cannot claim

The original API response is gone. Whatever is established here is an
**inference from a later response**, not the year the source stated at the time.
Those are different claims and the status says which one it is:
`recovered_inferred`, never "stated".

An earlier draft of this script called it `stated`. That would have put an
unearned claim on forty-five thousand rows, and the claim would then have been
read back as fact by everything downstream.

## How the inference works, and where it stops

The API returns each indicator as a series of (year, value). A stored figure is
matched against that series by reproducing the ingester's own rounding --
`round(float(v), 2)`, app/external_data.py:123,159,196 -- rather than by a
tolerance. A tolerance is not the inverse of a rounding: the first draft used
`max(abs(value) * 1e-6, 0.005)`, which at the largest stored value (114769.01)
admits 0.115, twenty-three times what the rounding can hide, and wide enough to
take a neighbouring year.

Every outcome short of a single candidate is recorded as itself:

  ambiguous                  several years hold this value
  no_match_current_vintage   the current data has no candidate. Revision is one
                             explanation; a changed dataset, indicator, unit or
                             geography are others, and this does not choose
  outside_query_window       the answer was truncated, so absence proves nothing
  source_unavailable         no conclusive answer; retry
  period_not_applicable      a derived value with no period to have

`unrecoverable` is deliberately not among them. It would mean the archives and
older vintages had been exhausted, and none has been consulted.

## Safety

**Row identities are materialised before any write.** Selecting by
(country, indicator, value) and then updating by the same triple would let an
ingester writing concurrently be caught by an UPDATE whose evidence never
covered it.

Every verdict records the run that made it, the rule version, the number of
candidates, the source vintage and a hash of the response it was made from --
so a wrong run can be found and undone without touching anyone else's work.
Re-running with a newer rule revisits what older rules decided; without that,
"idempotent" would mean only "resumable", never "fixable".

Dry by default.

    python scripts/backfill_indicator_periods.py                  # report only
    python scripts/backfill_indicator_periods.py --apply
    python scripts/backfill_indicator_periods.py --recheck-rule value-match/1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
import uuid
from collections import defaultdict
from datetime import datetime, timezone

import psycopg2

# Overridable so the write path can be driven against a stub.
#
# The first attempt at an integration test could not run this script at all, so
# it re-implemented the SQL and asserted against its own copy -- which passes
# forever, including on the day the script regresses. A test that cannot execute
# the thing it is named after is not testing it.
#
# This opens no door: anyone who can set an environment variable here can
# already set DATABASE_URL, and that is the far shorter path to the data.
API_ROOT = os.environ.get(
    "SORA_WORLDBANK_API_BASE", "https://api.worldbank.org/v2"
).rstrip("/")
API = API_ROOT + "/country/{iso}/indicator/{ind}"

# No date bound. The whole series, every time.
#
# The first draft asked for mrv=40 and treated a miss as evidence of revision. It
# is not: a row can describe a year older than the window, and absence outside
# the search says nothing at all. That was replaced by an explicit 1960-2030,
# which is the same defect with a wider window -- a value from outside it would
# have been recorded as `no_match_current_vintage`, which asserts the source
# revised the figure away, when the truth is that nobody looked there.
#
# Measured 2026-08-04 over the whole dataset for the four indicators this
# touches, country=all with no date filter. The API returns a row per
# country-year whether or not it holds a figure, so the count that matters is
# the second one:
#
#   indicator          rows returned   rows with a value   years with a value
#   SP.DYN.LE00.IN            17,490              17,126   1960-2024
#   NY.GDP.PCAP.CD            17,490              14,745   1960-2025
#   EG.FEC.RNEW.ZS            17,490               8,203   1990-2022
#   SI.POV.GINI               17,490               2,430   1963-2025
#
# 17,490 is identical across all four because it is the country x year grid, not
# an observation count -- quoting it alone would describe the shape of the
# response rather than the data. Nothing fell outside 1960-2030, so the bound
# cost nothing. It is gone anyway: that measurement describes today's four
# indicators and would have to be repeated, silently and by someone who knew to,
# for every one added later.
PER_PAGE = 500

DELAY_SECONDS = 1.5
RETRIES = 3

RECOVERED = "recovered_inferred"
AMBIGUOUS = "ambiguous"
NO_MATCH = "no_match_current_vintage"
OUTSIDE_WINDOW = "outside_query_window"
UNAVAILABLE = "source_unavailable"
NOT_APPLICABLE = "period_not_applicable"

METHOD = "value_match"
# Bumped whenever the rule changes. --recheck-rule revisits what an older one
# decided.
RULE_VERSION = "value-match/2"

# Sources that publish no period: app/external_data.py returns None for their
# period explicitly. Note this is a statement about the *source*, not about
# whether a derived period could be constructed -- an end-of-window or a
# calculation date might be meaningful, and neither is invented here.
PERIODLESS_SOURCES = ("benchmark", "global_avg")


def connect():
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set")
    return psycopg2.connect(url.replace("postgresql+psycopg://", "postgresql://"))


def fetch_history(iso: str, ind: str):
    """Everything the source will say about one indicator, across every page.

    Returns (history, meta) or (None, None) when no conclusive answer was
    obtained. None is not an empty history: one means "ask again", the other
    means "the source has nothing right now", and only the second is evidence.

    Pages are followed. An earlier version read only the first response and set
    a `truncated` flag from its page count -- and `classify` consulted that flag
    only when it had found no candidate. A unique match on page one was
    therefore recorded as recovered while a second match sat unread on page two:
    a check that existed, and could not fire in the case that needed it.
    """
    history: dict = {}
    hashes = []
    page = 1
    while True:
        url = (API.format(iso=iso, ind=ind)
               + f"?format=json&per_page={PER_PAGE}&page={page}")
        raw = None
        for attempt in range(RETRIES):
            try:
                raw = urllib.request.urlopen(url, timeout=30).read()
                break
            except Exception:
                if attempt == RETRIES - 1:
                    # One unobtainable page makes the whole answer inconclusive.
                    # Returning what arrived so far would let a partial read
                    # produce a verdict, which is the defect above wearing a
                    # different hat.
                    return None, None
                time.sleep(2 ** attempt * 2)

        payload = json.loads(raw.decode())
        header = payload[0] if payload else {}
        rows = payload[1] if len(payload) > 1 and payload[1] else []
        history.update({r["date"]: r["value"] for r in rows if r.get("value") is not None})
        hashes.append(hashlib.sha256(raw).hexdigest())

        pages = int(header.get("pages", 1) or 1)
        if page >= pages:
            meta = {
                "vintage": header.get("lastupdated"),
                # Over every page that formed the answer, so the hash identifies
                # what the verdict was actually made from.
                "sha256": hashlib.sha256("".join(hashes).encode()).hexdigest(),
                "pages": pages,
            }
            return history, meta
        page += 1
        time.sleep(DELAY_SECONDS)


def candidate_years(value: float, history: dict) -> list:
    """Every year whose source figure, rounded as the ingester rounded it, is
    this value.

    The original operation is reproduced rather than approximated, so the
    boundary cases are the ingester's own and not a second set introduced here.
    """
    return sorted(y for y, v in history.items() if round(float(v), 2) == value)


def classify(value, history, meta):
    """(status, year, candidate_count) for one stored value.

    OUTSIDE_WINDOW is reachable only if a caller hands over an answer it knows
    to be incomplete; fetch_history refuses to return one, and since the date
    bound was removed there is no window left to fall outside of. The status
    stays in the vocabulary because the question it answers -- "did the search
    cover the whole history?" -- is real, and a future caller working from an
    archive or a narrowed query will need to say no.
    """
    if history is None:
        return UNAVAILABLE, None, None
    if meta.get("incomplete"):
        # Checked before the candidates, not after. Consulted only on the
        # zero-candidate path, it could never fire for the case that matters:
        # a single match found in the part that was read.
        return OUTSIDE_WINDOW, None, None
    years = candidate_years(value, history)
    if len(years) == 1:
        return RECOVERED, years[0], 1
    if len(years) > 1:
        return AMBIGUOUS, None, len(years)
    return NO_MATCH, None, 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the results; without it nothing changes")
    parser.add_argument("--limit-pairs", type=int, default=0,
                        help="stop after this many country/indicator pairs")
    parser.add_argument("--recheck-rule", metavar="VERSION",
                        help="also revisit rows decided by this rule version")
    args = parser.parse_args()

    run_id = str(uuid.uuid4())
    print(f"  run {run_id}   rule {RULE_VERSION}   {'APPLY' if args.apply else 'dry'}")

    con = connect()
    cur = con.cursor()

    # Rows this run may touch: never decided, or decided by a rule being
    # rechecked. Without the second clause a corrected rule could never reach
    # what the old one got wrong.
    eligible = "(period_status IS NULL"
    params: list = []
    if args.recheck_rule:
        eligible += " OR period_rule_version = %s"
        params.append(args.recheck_rule)
    eligible += ")"

    # --- values whose source publishes no period ---------------------------
    cur.execute(
        f"""SELECT id FROM country_indicator_history
            WHERE {eligible} AND source = ANY(%s)""",
        (*params, list(PERIODLESS_SOURCES)),
    )
    periodless_ids = [r[0] for r in cur.fetchall()]

    if args.apply and periodless_ids:
        cur.execute(
            """UPDATE country_indicator_history
               SET period_status = %s, period_run_id = %s, period_method = %s,
                   period_rule_version = %s, period_resolved_at = %s
               WHERE id = ANY(%s)""",
            (NOT_APPLICABLE, run_id, "source_publishes_none", RULE_VERSION,
             datetime.now(timezone.utc), periodless_ids),
        )
        con.commit()

    # --- values the source dates -------------------------------------------
    #
    # Identities are fixed here. Updating by (country, indicator, value) would
    # reach rows the ingester wrote after this read, which no evidence in this
    # run covers.
    # No `as_of_date IS NULL` here.
    #
    # It was there, and it excluded exactly the rows a recheck exists for: a
    # `recovered_inferred` row carries a date, so a corrected rule could never
    # reach the verdicts the old rule got wrong. Undecided rows are still
    # selected by period_status IS NULL, which they satisfy on their own.
    cur.execute(
        f"""SELECT id, country_iso3, indicator_code, value
            FROM country_indicator_history
            WHERE {eligible}
              AND (period_status IS NOT NULL OR as_of_date IS NULL)
              AND source <> ALL(%s)""",
        (*params, list(PERIODLESS_SOURCES)),
    )
    by_pair: dict = defaultdict(lambda: defaultdict(list))
    for row_id, iso, ind, value in cur.fetchall():
        by_pair[(iso, ind)][value].append(row_id)

    pairs = sorted(by_pair)
    if args.limit_pairs:
        pairs = pairs[: args.limit_pairs]

    tally: dict = defaultdict(int)

    for i, key in enumerate(pairs, 1):
        iso, ind = key
        history, meta = fetch_history(iso, ind)
        time.sleep(DELAY_SECONDS)

        for value, row_ids in by_pair[key].items():
            status, year, candidates = classify(value, history, meta or {})
            tally[status] += len(row_ids)

            if status == UNAVAILABLE:
                # Left untouched, not marked. Recording a refusal as a verdict
                # would retire the row from every future attempt.
                continue
            if not args.apply:
                continue

            cur.execute(
                # Assigned, not coalesced.
                #
                # COALESCE kept the old date when the new verdict had none, so a
                # row reclassified from recovered_inferred to ambiguous would
                # have carried the date its discredited verdict produced -- and
                # the CHECK constraint forbids that pairing, so the correction
                # would have failed loudly at best and been believed at worst.
                """UPDATE country_indicator_history
                   SET as_of_date = %s,
                       period_status = %s,
                       period_run_id = %s,
                       period_method = %s,
                       period_rule_version = %s,
                       period_candidates = %s,
                       period_source_vintage = %s,
                       period_response_sha256 = %s,
                       period_resolved_at = %s
                   WHERE id = ANY(%s)""",
                (datetime(int(year), 1, 1) if year else None,
                 status, run_id, METHOD, RULE_VERSION, candidates,
                 (meta or {}).get("vintage"), (meta or {}).get("sha256"),
                 datetime.now(timezone.utc), row_ids),
            )

        if args.apply:
            con.commit()
        if i % 20 == 0:
            print(f"  ... {i}/{len(pairs)} pairs", flush=True)

    con.commit()

    verb = "set" if args.apply else "would set"
    print()
    print(f"  {verb} {NOT_APPLICABLE:26} {len(periodless_ids):>7}")
    for status in (RECOVERED, AMBIGUOUS, NO_MATCH, OUTSIDE_WINDOW):
        print(f"  {verb} {status:26} {tally[status]:>7}")
    print(f"  left for a later run       {tally[UNAVAILABLE]:>7}  "
          f"(no conclusive answer; a refusal is not a verdict)")
    if not args.apply:
        print()
        print("  nothing was written. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
