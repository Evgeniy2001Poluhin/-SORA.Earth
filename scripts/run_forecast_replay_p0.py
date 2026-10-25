#!/usr/bin/env python3
"""Explicit local research commands: plan, acquire, audit, evaluate. No production.

The source and protocol must be committed. Acquiring creates a new directory,
never overwrites one. Evaluating requires an externally pinned manifest hash.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.forecasting.replay_p0 import (
    ReplayRefusal, _write_new, acquire_snapshot, canonical, evaluate_snapshot,
    frozen_protocol, read_snapshot, request_plan,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan")
    for command in ("acquire", "audit", "evaluate"):
        child = sub.add_parser(command)
        child.add_argument("snapshot", type=Path)
        if command in ("audit", "evaluate"):
            child.add_argument("--manifest-sha256", required=True)
        if command == "evaluate":
            child.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        protocol, provenance = frozen_protocol()
        if args.command == "plan":
            requests, weighted = request_plan(protocol)
            result = {"provenance": provenance, "protocol": protocol, "requests": requests,
                      "estimated_weighted_calls": weighted}
        elif args.command == "acquire":
            manifest = acquire_snapshot(args.snapshot, protocol, provenance,
                                        progress=lambda message: print(message, file=sys.stderr, flush=True))
            from app.services.forecasting.replay_p0 import digest
            result = {"status": "acquired", "manifest_sha256": digest(canonical(manifest)),
                      "entries": len(manifest["entries"]), "bytes": manifest["bytes"]}
        else:
            from app.services.forecasting.replay_p0 import need
            if args.command == "audit":
                manifest, pin, _ = read_snapshot(args.snapshot, protocol, args.manifest_sha256)
                result = {"status": "whole_grid_and_pins_verified", "manifest_sha256": pin,
                          "entries": len(manifest["entries"]), "label": manifest["label"]}
            else:
                need(not args.output.exists() and args.output.parent.is_dir(), "report output must be new")
                result = evaluate_snapshot(args.snapshot, protocol, provenance, args.manifest_sha256)
                result["runtime_versions"] = {"python": platform.python_version(),
                                              "numpy": np.__version__, "pandas": pd.__version__}
                _write_new(args.output, canonical(result))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        return 0
    except (ReplayRefusal, OSError, KeyError, TypeError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, ReplayRefusal) else type(exc).__name__
        print(json.dumps({"status": "refused", "reason": reason}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
