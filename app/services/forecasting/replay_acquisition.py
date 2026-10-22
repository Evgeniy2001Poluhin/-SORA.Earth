"""One bounded explicit batch. No authorization, daemon, quota sleep or retry."""
import fcntl
import os
from pathlib import Path

from .replay_cache_worker import CacheWorker, need


def run_batch(root: Path, *, recovery_pin, continuation_pin, code_commit,
              max_requests, max_seconds, fetch, clock, monotonic, sleep, guard):
    need(type(max_requests) is int and 1 <= max_requests <= 40, 'batch request bound required')
    need(type(max_seconds) is int and 1 <= max_seconds <= 600, 'batch time bound required')
    need(root.is_dir() and not root.is_symlink(), 'owned cache root required')
    fd = os.open(root/'controller.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('controller already running') from exc
        worker = CacheWorker(root, recovery_pin=recovery_pin, continuation_pin=continuation_pin)
        start = monotonic()
        with worker.locked(clock()) as (state, _, _):
            need(state.get('calendar_continuation'), 'explicitly authorized continuation required')
        count = 0
        while count < max_requests:
            elapsed = monotonic()-start
            need(elapsed >= 0, 'monotonic clock rollback')
            if elapsed >= max_seconds:
                return {'status':'batch_time_limit', 'downloaded':count, 'automatic_retries':0}
            guard()
            def checked_fetch(*args):
                guard()  # Also recheck at the transport boundary, after reservation.
                return fetch(*args)
            result = worker.step(now=clock(), fetch=checked_fetch, code_commit=code_commit, clock=clock)
            if result['status'] != 'downloaded':
                return {**result, 'downloaded':count, 'automatic_retries':0}
            count += 1
            if count < max_requests:
                sleep(4)  # Longer quota waits are returned to the operator, never hidden.
        return {'status':'batch_request_limit', 'downloaded':count, 'automatic_retries':0}
    finally:
        os.close(fd)
