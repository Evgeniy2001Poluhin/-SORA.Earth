#!/usr/bin/env python3
"""Initialize the census namespace, verify it offline, publish, or perform exactly one step.

No loops and no retries. Exit codes: 0 completed step or verified mode, 3 paused
(a global STOP), 2 refused before any state change that could be read as progress.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.forecasting.replay_source_census import BUNDLE_FILES, CensusCollector
from app.services.forecasting.replay_cache_worker import MAX_STATE, bounded_fetch, need
from app.services.forecasting.replay_availability_inventory import _read


def source_guard(commit):
    actual = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], timeout=10).decode().strip()
    dirty = subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain'], timeout=10)
    need(actual == commit and not dirty, 'exact clean execution source required')


def read_bundle(directory):
    need(directory is not None and directory.is_dir() and not directory.is_symlink(),
         'explicit bundle directory required')
    return {name: _read(directory / name, MAX_STATE) for name in sorted(BUNDLE_FILES)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('parent', 'gap', 'root', 'policy'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--policy-sha256', required=True)
    parser.add_argument('--execution-commit', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--initialize', action='store_true')
    mode.add_argument('--preflight', action='store_true', help='offline; zero requests')
    mode.add_argument('--publish', action='store_true', help='read-only publication')
    parser.add_argument('--bundle-dir', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.initialize or args.preflight:
            source_guard(args.execution_commit)
        collector = CensusCollector(args.parent, args.gap, args.root, _read(args.policy, MAX_STATE),
                                    args.policy_sha256, code_commit=args.execution_commit)
        guard = lambda: source_guard(args.execution_commit)
        clock = lambda: datetime.now(timezone.utc)
        if args.initialize:
            result = collector.initialize(read_bundle(args.bundle_dir), now=clock(), guard=guard)
        elif args.preflight:
            result = collector.preflight_offline(read_bundle(args.bundle_dir), now=clock())
        elif args.publish:
            need(args.bundle_dir is None, 'publication reads the census namespace only')
            result = collector.publication(now=clock())
        else:
            need(args.bundle_dir is None, 'bundle directory applies only to initialization or preflight')
            result = collector.step(fetch=bounded_fetch, clock=clock, guard=guard)
    except Exception as exc:
        print(json.dumps({'status': 'refused', 'error_class': type(exc).__name__}), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 3 if result.get('status') == 'paused' else 0


if __name__ == '__main__':
    sys.exit(main())
