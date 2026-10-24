#!/usr/bin/env python3
"""Initialize a pinned companion ledger or perform exactly one reviewed step."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.forecasting.replay_gap_collector import CompanionCollector, BUNDLE
from app.services.forecasting.replay_cache_worker import bounded_fetch, need, MAX_STATE
from app.services.forecasting.replay_availability_inventory import _read


def source_guard(commit):
    actual = subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],timeout=10).decode().strip()
    dirty = subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],timeout=10)
    need(actual == commit and not dirty, 'exact clean execution source required')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('parent','root','policy'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--policy-sha256',required=True)
    parser.add_argument('--execution-commit',required=True)
    parser.add_argument('--initialize',action='store_true')
    parser.add_argument('--bundle-dir',type=Path)
    args = parser.parse_args()
    source_guard(args.execution_commit)
    worker = CompanionCollector(args.parent,args.root,_read(args.policy,MAX_STATE),args.policy_sha256,
                                code_commit=args.execution_commit)
    guard = lambda:source_guard(args.execution_commit)
    clock = lambda:datetime.now(timezone.utc)
    if args.initialize:
        need(args.bundle_dir is not None and args.bundle_dir.is_dir() and
             not args.bundle_dir.is_symlink(), 'explicit metadata bundle directory required')
        result = worker.initialize({k:_read(args.bundle_dir/k,MAX_STATE) for k in BUNDLE},now=clock(),guard=guard)
    else:
        need(args.bundle_dir is None, 'bundle directory applies only to initialization')
        result = worker.step(fetch=bounded_fetch,clock=clock,guard=guard)
    print(json.dumps(result,sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status':'refused','error_class':type(exc).__name__}),file=sys.stderr)
        sys.exit(2)
