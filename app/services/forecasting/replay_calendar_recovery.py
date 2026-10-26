"""One explicitly pinned retry of a saved upstream streaming-error response.

Original policies, quota history, failure bytes and checkpoints stay immutable.
This module neither authorizes itself nor performs network requests.
"""
import os
from pathlib import Path
import re

from .replay_cache_worker import (
    CacheWorker, MAX_STATE, SHA, need, read_regular, digest, parse_json,
    stamp, utc, save_state, sync_dir,
)
from .replay_cache_recovery import _preserve
from .replay_cache_continuation import CAPS, validate_active as parent_active

BACKUP = 'failed-calendar-state.json'
AMENDMENT = 'calendar-retry.json'
BAD_RESPONSE = 'failed-calendar-response.bin'
STREAM_ERROR = b'Unexpected error while streaming data: timeoutReached'


def validate_amendment(raw, pin, root, recovery_pin, continuation_pin):
    need(isinstance(pin, str) and SHA.fullmatch(pin) and digest(raw) == pin,
         'independent calendar retry pin required')
    p = parse_json(raw)
    fields = {'schema', 'id', 'canonical_ledger', 'failed_state_sha256',
              'failed_response_sha256', 'failed_day', 'recovery_amendment_sha256',
              'continuation_sha256', 'execution_commit', 'allowed_missing_dates',
              'max_total_attempts', 'max_total_reserved_units', 'retry_limit',
              'caps', 'scientific_changes', 'on_failure'}
    need(isinstance(p, dict) and set(p) == fields, 'calendar retry fields differ')
    need(all(type(p[k]) is int for k in ('schema', 'retry_limit', 'max_total_attempts',
                                         'max_total_reserved_units')), 'integer retry policy required')
    need(p['schema'] == 1 and p['id'] == 'M3-P1-CALENDAR-ONE-RETRY' and
         p['retry_limit'] == 1 and p['scientific_changes'] is False and
         p['on_failure'] == 'stop_no_more_retries', 'unreviewed calendar retry scope')
    need(p['canonical_ledger'] == str(root.resolve()), 'retry ledger path differs')
    for key in ('failed_state_sha256', 'failed_response_sha256',
                'recovery_amendment_sha256', 'continuation_sha256'):
        need(isinstance(p[key], str) and SHA.fullmatch(p[key]), 'retry source pin required')
    need(p['recovery_amendment_sha256'] == recovery_pin and
         p['continuation_sha256'] == continuation_pin, 'original two pins required')
    need(isinstance(p['execution_commit'], str) and re.fullmatch(r'[0-9a-f]{40}', p['execution_commit']),
         'retry execution source required')
    need(p['caps'] == CAPS, 'retry cannot increase rolling quota')
    need(p['failed_response_sha256'] == digest(STREAM_ERROR), 'unrecognized upstream error body')
    return p


def validate_failed(root, original, p, recovery_pin, continuation_pin, now, requests):
    need(not original.get('calendar_retry') and original['paused'] == 'request_failed:ReplayRefusal',
         'only first reviewed calendar failure is recoverable')
    parent = parent_active(root, original, continuation_pin, recovery_pin, now, requests)
    need(original['inflight'] == {'day': p['failed_day'], 'response_sha256': p['failed_response_sha256'],
                                  'source': {'kind': 'reserved_http_attempt'}}, 'failed response identity differs')
    events = original['events']
    attempts = [e for e in events if e['kind'] == 'attempt']
    need(attempts and events[-1] == attempts[-1] and attempts[-1]['day'] == p['failed_day'] and
         attempts[-1].get('calendar_continuation_sha256') == continuation_pin and
         'reserved_at' in attempts[-1] and
         stamp(attempts[-1]['reserved_at']) <= stamp(attempts[-1]['at']) <= stamp(original['last_seen']),
         'failed completion timing/history differs')
    missing = [d for d in parent['allowed_missing_dates'] if d not in original['completed']]
    need(missing and missing[0] == p['failed_day'] and p['allowed_missing_dates'] == missing,
         'retry remaining sequence differs')
    need(p['max_total_attempts'] == len(attempts) + len(missing) and
         p['max_total_reserved_units'] == sum(e['weight'] for e in events) + 21 * len(missing),
         'retry total budget differs')


def validate_active(root, state, pin, recovery_pin, continuation_pin, now, requests):
    p = validate_amendment(read_regular(root / AMENDMENT, 100000), pin, root,
                           recovery_pin, continuation_pin)
    raw = read_regular(root / BACKUP, MAX_STATE)
    need(digest(raw) == p['failed_state_sha256'], 'failed calendar checkpoint changed')
    original = parse_json(raw)
    validate_failed(root, original, p, recovery_pin, continuation_pin, now, requests)
    need(read_regular(root / BAD_RESPONSE, 1000) == STREAM_ERROR, 'archived failure bytes changed')
    record = state['calendar_retry']
    need(record['amendment_sha256'] == pin and
         stamp(original['last_seen']) <= stamp(record['enabled_at']) <= now,
         'calendar retry authorization differs')
    for key in ('code_commit', 'plan_sha256', 'manual_recovery'):
        need(state[key] == original[key], 'parent identity changed')
    # Fail closed even in frozen pre-retry code: its continuation validator
    # requires the old policy pin here. The original record is immutable in BACKUP.
    need(state['calendar_continuation'] == {**original['calendar_continuation'], 'policy_sha256':pin},
         'effective retry authorization differs')
    n = len(original['events'])
    need(state['events'][:n] == original['events'] and
         all(state['completed'].get(d) == receipt for d, receipt in original['completed'].items()),
         'old reservation or completion changed')
    new = state['events'][n:]
    need(len(new) <= len(p['allowed_missing_dates']) and
         [e['day'] for e in new] == p['allowed_missing_dates'][:len(new)],
         'retry sequence differs or attempt repeated')
    previous = stamp(record['enabled_at'])
    for i, event in enumerate(new):
        start, end = stamp(event.get('reserved_at', event['at'])), stamp(event['at'])
        need(event['kind'] == 'attempt' and event['weight'] == 21 and
             event.get('calendar_continuation_sha256') == continuation_pin and
             previous <= start <= end <= now, 'invalid retry reservation')
        if i == 0:
            need(event.get('attempt_ordinal') == 2 and event.get('recovery_amendment_sha256') == pin,
                 'retry lacks explicit ordinal/amendment')
        previous = end
    need(len([e for e in state['events'] if e['kind'] == 'attempt']) <= p['max_total_attempts'] and
         sum(e['weight'] for e in state['events']) <= p['max_total_reserved_units'], 'retry budget exceeded')
    new_days = {e['day'] for e in new}
    need(set(state['completed']) <= set(original['completed']) | new_days,
         'completion without retry reservation')
    if state['inflight']:
        need(new and state['inflight']['day'] == new[-1]['day'] and
             state['inflight']['day'] not in state['completed'] and
             state['inflight']['source']['kind'] == 'reserved_http_attempt', 'unexpected retry inflight')
    return {**p, 'retry_pin': pin, 'completion_status': 'all_downloaded_pending_final_audit'}


class CalendarRecoveryWorker(CacheWorker):
    def __init__(self, root, recovery_pin, continuation_pin, calendar_retry_pin):
        super().__init__(root, recovery_pin=recovery_pin, continuation_pin=continuation_pin)
        self.calendar_retry_pin = calendar_retry_pin

    def recovery_policy(self, state, now, requests):
        if state.get('calendar_retry'):
            return validate_active(self.root, state, self.calendar_retry_pin,
                                   self.recovery_pin, self.continuation_pin, now, requests)
        return super().recovery_policy(state, now, requests)


def authorize_once(root: Path, raw, pin, recovery_pin, continuation_pin, *, now, code_commit):
    """Offline, crash-resumable; move only the exact known failed response, never delete it."""
    utc(now)
    p = validate_amendment(raw, pin, root, recovery_pin, continuation_pin)
    need(code_commit == p['execution_commit'], 'retry execution source differs')
    worker = CalendarRecoveryWorker(root, recovery_pin, continuation_pin, pin)
    with worker.locked(now) as (state, _, requests):
        if state.get('calendar_retry'):
            return {'status': 'already_authorized', 'paused': state['paused'], 'network_requests': 0}
        original_raw = read_regular(root / 'state.json', MAX_STATE)
        need(digest(original_raw) == p['failed_state_sha256'], 'failed state pin differs')
        validate_failed(root, parse_json(original_raw), p, recovery_pin, continuation_pin, now, requests)
        target = root / 'raw' / requests[p['failed_day']]['file']
        archive = root / BAD_RESPONSE
        if target.exists() or target.is_symlink():
            need(read_regular(target, 1000) == STREAM_ERROR and
                 not archive.exists() and not archive.is_symlink(), 'unreviewed response/archive collision')
        else:
            need(read_regular(archive, 1000) == STREAM_ERROR, 'failed response missing or changed')
        _preserve(root / BACKUP, original_raw)
        _preserve(root / AMENDMENT, raw)
        if target.exists():
            os.rename(target, archive)
        sync_dir(target.parent)
        sync_dir(root)
        need(read_regular(archive, 1000) == STREAM_ERROR, 'archive not preserved')
        state['calendar_retry'] = {'amendment_sha256': pin, 'enabled_at': now.isoformat()}
        state['calendar_continuation'] = {**state['calendar_continuation'], 'policy_sha256':pin}
        state['paused'], state['inflight'] = None, None
        save_state(root, state)
    return {'status': 'authorized_calendar_retry', 'network_requests': 0, 'retry_limit': 1}
