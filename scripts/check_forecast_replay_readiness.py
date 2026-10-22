#!/usr/bin/env python3
"""Check a supplied replay metadata plan offline; no network, fitting or scoring.

Exit 0 means metadata consistency only. A refused plan exits 2. Existing M3
entry gates, protocols, source authentication and scientific review still apply.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.forecasting.replay_readiness import ReplayPlanError, check_replay_plan

MAX_BYTES = 16 * 1024 * 1024


def _object_without_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReplayPlanError("JSON object has a duplicate key")
        result[key] = value
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path, help="Metadata JSON only, not an archive directory")
    args = parser.parse_args(argv)
    try:
        with args.plan.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ReplayPlanError("metadata plan exceeds 16 MiB")
        plan = json.loads(raw, object_pairs_hook=_object_without_duplicates)
        report = check_replay_plan(plan)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        reason = str(exc) if isinstance(exc, ReplayPlanError) else type(exc).__name__
        print(json.dumps({"status": "refused", "reason": reason}))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
