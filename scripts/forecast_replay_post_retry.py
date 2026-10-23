#!/usr/bin/env python3
"""Explicit post-retry authorization/status/audit or one bounded collection batch."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.forecasting.replay_cache_worker import read_regular, bounded_fetch, canonical, need
from app.services.forecasting.replay_post_retry_continuation import PostRetryWorker, authorize_once
from app.services.forecasting.replay_acquisition import run_batch


def frozen_head():
    need(not subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, timeout=10).strip(),
         'execution source must be committed and clean')
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, timeout=10, text=True).strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('authorize', 'status', 'audit', 'batch'))
    parser.add_argument('--cache', type=Path, required=True)
    for name in ('recovery', 'continuation', 'calendar-retry', 'transport-retry', 'post-retry-continuation'):
        parser.add_argument('--' + name + '-sha256', required=True)
    parser.add_argument('--confirm-code-commit', required=True)
    parser.add_argument('--amendment', type=Path)
    parser.add_argument('--max-requests', type=int)
    parser.add_argument('--max-seconds', type=int)
    args = parser.parse_args(argv)
    try:
        head = frozen_head()
        def guard():
            need(frozen_head() == args.confirm_code_commit, 'execution source differs')
        guard()
        now = datetime.now(timezone.utc)
        parents = (args.recovery_sha256, args.continuation_sha256,
                   args.calendar_retry_sha256, args.transport_retry_sha256)
        pin = args.post_retry_continuation_sha256
        worker = PostRetryWorker(args.cache, *parents, pin)
        if args.command == 'authorize':
            need(args.amendment is not None, 'explicit amendment required')
            result = authorize_once(args.cache, read_regular(args.amendment, 100000), pin,
                                    *parents, now=now, code_commit=head)
        elif args.command == 'batch':
            result = run_batch(args.cache, recovery_pin=parents[0], continuation_pin=parents[1],
                               calendar_retry_pin=parents[2], transport_retry_pin=parents[3],
                               post_retry_continuation_pin=pin, code_commit=head,
                               max_requests=args.max_requests, max_seconds=args.max_seconds,
                               fetch=bounded_fetch, clock=lambda: datetime.now(timezone.utc),
                               monotonic=time.monotonic, sleep=time.sleep, guard=guard)
        elif args.command == 'audit':
            result = worker.audit(now=now)
        else:
            with worker.locked(now) as (state, _, requests):
                result = {'status': 'local_status', 'completed': len(state['completed']),
                          'expected': len(requests), 'paused': state['paused'],
                          'inflight': state['inflight'], 'network_requests': 0}
        print(canonical(result).decode(), end='')
        return 4 if result['status'] == 'paused' else 3 if result['status'] == 'quota_wait' else 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(canonical({'status': 'refused', 'error_class': type(exc).__name__}).decode(),
              end='', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
