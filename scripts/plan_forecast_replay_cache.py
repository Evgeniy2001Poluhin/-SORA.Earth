#!/usr/bin/env python3
"""Print a deterministic local plan; never downloads, fits, scores or writes files."""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.forecasting.replay_cache_plan import P0_PROTOCOL_PIN, build_plan  # noqa: E402


def main() -> int:
    raw = (ROOT / "docs/research/M3_REPLAY_P0.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != P0_PROTOCOL_PIN:
        print("refused: P0 protocol changed; cache plan needs review", file=sys.stderr)
        return 2
    print(json.dumps(build_plan(), ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
