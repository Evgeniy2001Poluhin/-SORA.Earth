#!/usr/bin/env python3
"""Explicit offline authorization or ONE transport call; no acquisition loop."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.forecasting.replay_cache_worker import read_regular, bounded_fetch, canonical
from app.services.forecasting.replay_transport_recovery import TransportRetryWorker, authorize_once


def frozen_head():
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT).strip():
        raise ValueError('execution source must be committed and clean')
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('authorize', 'fetch-one', 'status'))
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--recovery-sha256', required=True)
    parser.add_argument('--continuation-sha256', required=True)
    parser.add_argument('--calendar-retry-sha256', required=True)
    parser.add_argument('--transport-retry-sha256', required=True)
    parser.add_argument('--confirm-code-commit', required=True)
    parser.add_argument('--amendment', type=Path)
    args = parser.parse_args(argv)
    try:
        head = frozen_head()
        if head != args.confirm_code_commit:
            raise ValueError('execution source differs')
        now = datetime.now(timezone.utc)
        pins = (args.recovery_sha256, args.continuation_sha256, args.calendar_retry_sha256)
        worker = TransportRetryWorker(args.cache, *pins, args.transport_retry_sha256)
        if args.command == 'authorize':
            if args.amendment is None:
                raise ValueError('explicit amendment required')
            result = authorize_once(args.cache, read_regular(args.amendment, 100000),
                                    args.transport_retry_sha256, *pins, now=now, code_commit=head)
        elif args.command == 'status':
            with worker.locked(now) as (state, _, requests):
                result = {'status': 'local_status', 'completed': len(state['completed']),
                          'expected': len(requests), 'paused': state['paused'],
                          'inflight': state['inflight'], 'network_requests': 0}
        else:
            result = worker.step(now=now, fetch=bounded_fetch, code_commit=head,
                                 clock=lambda: datetime.now(timezone.utc))
        print(canonical(result).decode(), end='')
        return 4 if result['status'] == 'paused' else 3 if result['status'] == 'quota_wait' else 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(canonical({'status': 'refused', 'error_class': type(exc).__name__}).decode(),
              end='', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
