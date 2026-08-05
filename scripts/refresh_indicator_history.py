#!/usr/bin/env python3
"""Run the World Bank history refresh once, with the counters in view.

The scheduled job that would otherwise do this is off by default
(`SORA_HISTORY_REFRESH`), because `auto_refresh_external_data` runs immediately
when the scheduler starts -- so enabling it would make the first mass
ingestion a side effect of a deployment, unobserved and racing anyone running
it by hand.

The intended sequence:

    deploy with the flag off
    python3 scripts/refresh_indicator_history.py        # this, watching it
    check the database with independent SQL
    python3 scripts/refresh_indicator_history.py        # inserted must be 0
    turn SORA_HISTORY_REFRESH on

It takes the same Redis lock the scheduled job takes, so a manual run and a
scheduled one cannot both be the first writer. If the lock is held, this
refuses rather than proceeding: two concurrent first runs against an empty
history is exactly the case worth refusing.

Exit codes
    0   the run completed
    2   the lock is held by someone else
    3   the refresh raised
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

LOCK_KEY = "sora:lock:external_refresh"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--indicator", action="append", default=None,
        help="restrict to one indicator code; repeatable. Default: all of INDICATORS",
    )
    parser.add_argument(
        "--country", action="append", default=None,
        help="restrict to one ISO3 code; repeatable. Default: all of COUNTRY_ISO3",
    )
    parser.add_argument(
        "--no-lock", action="store_true",
        help="skip the Redis lock. Only for a database nothing else writes to.",
    )
    parser.add_argument("--json", action="store_true", help="counters as JSON")
    args = parser.parse_args()

    from app.external_data import (
        COUNTRY_ISO3, INDICATORS, refresh_indicator_history,
    )

    countries = COUNTRY_ISO3
    if args.country:
        wanted = {c.upper() for c in args.country}
        countries = {n: i for n, i in COUNTRY_ISO3.items() if i.upper() in wanted}
        if not countries:
            print(f"no country matches {sorted(wanted)}", file=sys.stderr)
            return 3

    indicators = INDICATORS
    if args.indicator:
        wanted = set(args.indicator)
        indicators = {k: c for k, c in INDICATORS.items() if c in wanted}
        if not indicators:
            print(f"no indicator matches {sorted(wanted)}", file=sys.stderr)
            return 3

    lock = None
    if not args.no_lock:
        try:
            from app.locks import RedisLock

            lock = RedisLock(key=LOCK_KEY, timeout=3600)
            if not lock.acquire():
                print(
                    f"{LOCK_KEY} is held -- a refresh is already running. "
                    "Refusing rather than starting a second one.",
                    file=sys.stderr,
                )
                return 2
        except ImportError:
            print("redis lock unavailable; re-run with --no-lock if that is "
                  "acceptable for this database", file=sys.stderr)
            return 3

    started = time.time()
    print(f"start   {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    print(f"scope   {len(countries)} countries x {len(indicators)} indicators "
          f"= {len(countries) * len(indicators)} series")
    if not args.no_lock:
        print(f"lock    held: {LOCK_KEY}")
    sys.stdout.flush()

    try:
        stats = refresh_indicator_history(
            countries=countries, indicators=indicators,
            job_name="manual_history_refresh",
        )
    except Exception as e:  # noqa: BLE001 -- reported, then re-raised as a code
        print(f"FAILED after {time.time() - started:.1f}s: {e}", file=sys.stderr)
        return 3
    finally:
        if lock is not None:
            lock.release()

    elapsed = time.time() - started

    if args.json:
        print(json.dumps({**stats, "elapsed_sec": round(elapsed, 1)}))
    else:
        print()
        for key in ("fetched", "inserted", "unchanged", "revised", "no_value", "no_period"):
            print(f"  {key:<10} {stats[key]}")
        print(f"  {'elapsed':<10} {elapsed:.1f}s")
        print()
        # Stated here so the acceptance check does not depend on remembering
        # it: a second run of an unchanged source must insert nothing.
        if stats["inserted"] or stats["revised"]:
            print("  a re-run should now report inserted=0 revised=0 "
                  f"unchanged={stats['fetched'] - stats['no_value'] - stats['no_period']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
