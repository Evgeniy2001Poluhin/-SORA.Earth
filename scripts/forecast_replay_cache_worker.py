#!/usr/bin/env python3
"""Explicit single-step local worker; never starts a daemon or an acquisition loop."""
from pathlib import Path
import argparse
from datetime import datetime, timezone
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.forecasting.replay_cache_worker import (  # noqa: E402
    CacheWorker, initialize, read_regular, bounded_fetch,
)


def frozen_head():
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT).strip():
        raise ValueError("tracked sources must be clean")
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("directory", type=Path)
    init.add_argument("--plan", type=Path, required=True)
    init.add_argument("--plan-sha256", required=True)
    init.add_argument("--prior-reserved-units", type=int, required=True)
    for command in ("status", "audit", "fetch-one", "import-one"):
        part = sub.add_parser(command)
        part.add_argument("directory", type=Path)
        if command == "fetch-one":
            part.add_argument("--confirm-code-commit", required=True)
        if command == "import-one":
            part.add_argument("--day", required=True)
            part.add_argument("--source", type=Path, required=True)
            part.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    try:
        worker = CacheWorker(args.directory)
        if args.command == "init":
            initialize(args.directory, read_regular(args.plan, 1_048_576), args.plan_sha256,
                       prior_units=args.prior_reserved_units, now=now, code_commit=frozen_head())
            result = {"status": "initialized", "network_requests": 0}
        elif args.command == "import-one":
            result = worker.import_one(args.day, args.source, args.manifest_sha256, now=now)
        elif args.command == "audit":
            result = worker.audit(now=now)
        elif args.command == "status":
            with worker.locked(now) as (state, _, requests):
                result = {"status": "local_status", "completed": len(state["completed"]),
                          "expected": len(requests), "paused": state["paused"],
                          "inflight": state["inflight"], "quota_events": len(state["events"]),
                          "failure_diagnostic": state.get("failure_diagnostic"),
                          "network_requests": 0}
        else:
            head = frozen_head()
            if head != args.confirm_code_commit:
                raise ValueError("execution code differs from initialized commit")
            result = worker.step(now=now, fetch=bounded_fetch, code_commit=head,
                                 clock=lambda: datetime.now(timezone.utc))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        if result["status"] == "paused":
            return 4
        if result["status"] in ("quota_wait", "imports_required"):
            return 3
        if result["status"] == "audit" and not result["ready_for_review"]:
            return 3
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        # No arbitrary provider exception text, payload, URL or local paths.
        print(json.dumps({"status": "refused", "error_class": type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
