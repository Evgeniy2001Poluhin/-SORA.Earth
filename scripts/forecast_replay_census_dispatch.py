#!/usr/bin/env python3
"""Bounded approved census invocation. No scheduler, sleeping, retries, or inferred consent."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import subprocess

ROOT = Path(__file__).resolve().parents[1]
# Capture before importing application modules, then bind all later guards.
LOADED_COMMIT = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], timeout=10).decode().strip()
LOADED_CLEAN = not subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain'], timeout=10)
sys.path.insert(0, str(ROOT))
from app.services.forecasting.replay_source_census import BUNDLE_FILES, CensusCollector
from app.services.forecasting.replay_census_dispatcher import CensusDispatcher, loaded_source
from app.services.forecasting.replay_cache_worker import MAX_STATE, bounded_fetch
from app.services.forecasting.replay_availability_inventory import _read


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('parent', 'gap', 'root', 'policy', 'approval', 'journal-root'):
        p.add_argument('--' + name, type=Path, required=True)
    for name in ('policy-sha256', 'execution-commit', 'approval-sha256', 'run-id'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--mode', choices=('initialize', 'step'), required=True)
    p.add_argument('--steps', type=int, default=1)
    p.add_argument('--bundle-dir', type=Path)
    args = p.parse_args(argv)
    try:
        guard = lambda: loaded_source(ROOT, args.execution_commit,
                                       loaded_commit=LOADED_COMMIT, loaded_clean=LOADED_CLEAN)
        guard()
        c = CensusCollector(args.parent, args.gap, args.root, _read(args.policy, MAX_STATE),
                            args.policy_sha256, code_commit=args.execution_commit)
        bundle = ({n: _read(args.bundle_dir / n, MAX_STATE) for n in BUNDLE_FILES}
                  if args.mode == 'initialize' and args.bundle_dir is not None else None)
        result = CensusDispatcher(c, args.journal_root).run(
            approval_raw=_read(args.approval, MAX_STATE), approval_pin=args.approval_sha256,
            run_id=args.run_id, mode=args.mode, steps=args.steps,
            clock=lambda: datetime.now(timezone.utc), guard=guard,
            fetch=bounded_fetch if args.mode == 'step' else None, bundle=bundle)
        print(json.dumps(result, sort_keys=True))
        return 3 if result['status'] == 'paused' else 0
    except Exception as exc:
        print(json.dumps({'status': 'refused', 'error_class': type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
