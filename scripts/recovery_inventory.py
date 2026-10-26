#!/usr/bin/env python3
"""Read one metadata JSON, never any referenced backup, model, secret or URL."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.recovery_inventory import inspect_inventory

MAX_BYTES = 65536


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: recovery_inventory.py <metadata.json>", file=sys.stderr)
        return 2
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(args[0], flags), "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError("regular file required")
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("metadata too large")
        manifest = json.loads(raw, object_pairs_hook=_unique)
        result = inspect_inventory(manifest, now=datetime.now(timezone.utc))
    except (OSError, ValueError, TypeError, RecursionError, OverflowError):
        print('{"status":"invalid_input","error":"metadata_rejected"}')
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result["metadata_complete"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
