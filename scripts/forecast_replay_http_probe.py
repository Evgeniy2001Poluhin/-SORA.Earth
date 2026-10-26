#!/usr/bin/env python3
"""Exactly one explicit diagnostic request; no authorization/collection loop."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.forecasting.replay_cache_worker import read_regular, canonical, bounded_fetch, need
from app.services.forecasting.replay_http_probe import probe_once


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cache', type=Path, required=True)
    p.add_argument('--policy', type=Path, required=True)
    p.add_argument('--policy-sha256', required=True)
    p.add_argument('--parent-pin', action='append', required=True)
    p.add_argument('--confirm-code-commit', required=True)
    args = p.parse_args(argv)
    def guard():
        need(not subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, timeout=10).strip(),
             'committed clean source required')
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, timeout=10).strip()
        need(head == args.confirm_code_commit, 'execution source differs')
    try:
        guard()
        result = probe_once(args.cache, read_regular(args.policy, 100000), args.policy_sha256,
                            args.parent_pin, code_commit=args.confirm_code_commit, fetch=bounded_fetch,
                            clock=lambda: datetime.now(timezone.utc), guard=guard)
        print(canonical(result).decode(), end='')
        return 3 if result['status'] == 'quota_wait' else 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(canonical({'status': 'refused_or_probe_consumed_stop', 'error_class': type(exc).__name__}).decode(),
              end='', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
