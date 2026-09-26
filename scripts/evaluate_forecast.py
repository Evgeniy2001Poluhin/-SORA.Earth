#!/usr/bin/env python3
"""Produce the phase 6 report for the declared M3 target.

`docs/DEVELOPMENT_ROADMAP.md` phase 6 exits on "one reproducible report over one
snapshot; a baseline is mandatory". The harness that computes it has existed for
some time and nothing ran it -- measured 2026-09-20,
`app/services/forecasting/evaluation.py` was imported by tests and by no script,
endpoint or job. This is the caller.

    python scripts/evaluate_forecast.py                  # to stdout
    python scripts/evaluate_forecast.py -o report.json   # and to a file

What it prints today is a refusal with its reasons, and that is the correct
output: the §7 clock restarted on 2026-09-03 and h=7 needs 174 days of history,
so the earliest evidential run is 2027-02-24. The report fills in as the days
accumulate; nothing about it has to change when they do.

The point set is passed **from the declaration**, not taken from the data. Left
to the data, §7's "every declared region present" condition is vacuously true --
the set would be defined as whatever turned up -- and the report would pass a
condition that had not been tested. `build_report` records which of the two
happened either way.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_SOURCE = "openmeteo"
DEFAULT_INDICATOR = "temperature"


def declared_points():
    """The 21 points of `openmeteo-points-v1`, from the ingester's own list.

    §2 of the declaration names them as "the coordinates of a single capital
    taken from REGION_CAPITALS in app/ingesters/openmeteo.py", so that list is
    the set rather than a copy of it kept here -- a second copy is how two
    populations end up compared under one name, which §1.4 of the M2 protocol
    exists to forbid.
    """
    from app.ingesters.openmeteo import REGION_CAPITALS

    return sorted(code for code, _lat, _lon in REGION_CAPITALS)


def load_rows(source: str, indicator: str, days: int):
    """`(point, event_time, value)` for the target, straight from the table.

    `temporal_kind == 'observed'` because a forecast row is not an observation
    of anything, and mixing the two would score a model against its own output.

    The cutoff is `now() - interval`: an instant minus an interval, compared
    with `event_time` (timestamptz) as an instant. It was
    `(now() at time zone 'utc') - interval`, a zone-less UTC wall clock that
    PostgreSQL turned back into an instant with the *session's* TimeZone -- so
    the window moved by the session's offset, and the rows in the report
    depended on a connection setting.
    """
    from sqlalchemy import text

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        rows = db.execute(text(
            """
            SELECT region_id, event_time, value
              FROM environmental_observations
             WHERE source = :source
               AND indicator = :indicator
               AND temporal_kind = 'observed'
               AND event_time IS NOT NULL
               AND value IS NOT NULL
               AND event_time >= now() - make_interval(days => :days)
             ORDER BY event_time
            """
        ), {"source": source, "indicator": indicator, "days": days}).all()
        return [(r[0], r[1], float(r[2])) for r in rows]
    finally:
        db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--indicator", default=DEFAULT_INDICATOR)
    parser.add_argument("--days", type=int, default=800,
                        help="how far back to read, in whole days")
    parser.add_argument("-o", "--output", help="also write the report here")
    parser.add_argument("--include-shadow", action="store_true",
                        help="include shadow models (e.g., LSTM) in candidate evaluation")
    args = parser.parse_args(argv)

    from app.services.forecasting.report import build_report

    rows = load_rows(args.source, args.indicator, args.days)
    report = build_report(
        rows,
        target=f"{args.source}:{args.indicator}",
        declared_points=declared_points(),
        include_shadow=args.include_shadow,
    )

    text_out = json.dumps(report, indent=2, sort_keys=True, default=str)
    print(text_out)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text_out + "\n")

    # A refusal is not a failure of this script: it is the report's content.
    # Exit 0 either way, so a scheduled run does not page anyone for the gate
    # saying what it is supposed to say until February.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
