"""Pinned continuation of a completed recovery; never a new ledger or retry.

The byte-pinned checkpoint retains the old failure, attempts and receipts.
Both the original recovery pin and this continuation pin remain required.
"""
from pathlib import Path
import re

from .replay_cache_worker import (
    CacheWorker, MAX_STATE, SHA, need, read_regular, digest, parse_json,
    stamp, utc, save_state,
)
from .replay_cache_recovery import BACKUP, _preserve, validate_active as validate_recovery

CHECKPOINT = 'completed-stage1-before-continuation.json'
POLICY = 'calendar-continuation.json'
CAPS = {'rolling60s': 400, 'rolling3600s': 4000,
        'rolling86400s': 7000, 'spacing_seconds_after_response': 4}


def validate_policy(raw, pin, root, recovery_pin):
    need(isinstance(pin, str) and SHA.fullmatch(pin) and digest(raw) == pin,
         'independent continuation pin required')
    p = parse_json(raw)
    fields = {'schema', 'id', 'canonical_ledger', 'checkpoint_sha256',
              'recovery_amendment_sha256', 'execution_commit', 'allowed_missing_dates',
              'max_additional_attempts', 'max_total_attempts', 'max_total_reserved_units',
              'caps', 'retries', 'scientific_changes', 'on_failure'}
    need(isinstance(p, dict) and set(p) == fields, 'continuation fields differ')
    need(all(type(p[k]) is int for k in ('schema', 'retries', 'max_additional_attempts',
                                         'max_total_attempts', 'max_total_reserved_units')),
         'integer continuation policy required')
    need(p['schema'] == 1 and p['id'] == 'M3-P1-FULL-CALENDAR-2026-10-22' and
         p['retries'] == 0 and p['scientific_changes'] is False and
         p['on_failure'] == 'stop_no_retries', 'unreviewed continuation scope')
    need(p['canonical_ledger'] == str(root.resolve()), 'continuation ledger path differs')
    need(isinstance(p['checkpoint_sha256'], str) and SHA.fullmatch(p['checkpoint_sha256']) and
         isinstance(p['execution_commit'], str) and re.fullmatch(r'[0-9a-f]{40}', p['execution_commit']),
         'continuation source pins required')
    need(isinstance(recovery_pin, str) and SHA.fullmatch(recovery_pin) and
         p['recovery_amendment_sha256'] == recovery_pin, 'original recovery pin required')
    need(p['caps'] == CAPS, 'continuation cannot increase quota')
    return p


def validate_checkpoint(root, checkpoint, p, recovery_pin, now, requests):
    need(not checkpoint.get('calendar_continuation') and checkpoint['paused'] is None and
         checkpoint['inflight'] is None, 'settled first-slice checkpoint required')
    recovery = validate_recovery(root, checkpoint, recovery_pin, now, requests)
    original = parse_json(read_regular(root / BACKUP, MAX_STATE))
    need(set(checkpoint['completed']) == set(original['completed']) | set(recovery['allowed_missing_dates']) and
         len(checkpoint['events']) == len(original['events']) + len(recovery['allowed_missing_dates']),
         'first slice not complete')
    missing = sorted(set(requests) - checkpoint['completed'].keys())
    attempts = [e for e in checkpoint['events'] if e['kind'] == 'attempt']
    need(missing and p['allowed_missing_dates'] == missing and
         not (set(missing) & {e['day'] for e in attempts}),
         'continuation dates differ or repeat an attempted date')
    need(p['max_additional_attempts'] == len(missing) and
         p['max_total_attempts'] == len(attempts) + len(missing) and
         p['max_total_reserved_units'] == sum(e['weight'] for e in checkpoint['events']) + 21 * len(missing),
         'continuation budget differs')


def validate_active(root: Path, state, pin, recovery_pin, now, requests):
    """Under the existing worker lock; never edits the frozen parent checkpoint."""
    p = validate_policy(read_regular(root / POLICY, 100000), pin, root, recovery_pin)
    raw = read_regular(root / CHECKPOINT, MAX_STATE)
    need(digest(raw) == p['checkpoint_sha256'], 'completed checkpoint pin differs')
    checkpoint = parse_json(raw)
    validate_checkpoint(root, checkpoint, p, recovery_pin, now, requests)
    record = state['calendar_continuation']
    need(record['policy_sha256'] == pin and
         stamp(checkpoint['last_seen']) <= stamp(record['enabled_at']) <= now,
         'continuation authorization differs')
    for key in ('code_commit', 'plan_sha256', 'manual_recovery'):
        need(state[key] == checkpoint[key], 'parent source/plan/recovery identity changed')
    n = len(checkpoint['events'])
    need(state['events'][:n] == checkpoint['events'], 'parent quota events changed')
    need(all(state['completed'].get(d) == receipt for d, receipt in checkpoint['completed'].items()),
         'parent completion changed')
    new = state['events'][n:]
    need(len(new) <= p['max_additional_attempts'] and
         [e['day'] for e in new] == p['allowed_missing_dates'][:len(new)],
         'continuation sequence differs or attempts repeated')
    previous = stamp(record['enabled_at'])
    for event in new:
        start, finish = stamp(event.get('reserved_at', event['at'])), stamp(event['at'])
        need(event['kind'] == 'attempt' and event['weight'] == 21 and
             event.get('calendar_continuation_sha256') == pin and
             previous <= start <= finish <= now, 'invalid continuation reservation')
        previous = finish
    new_days = {e['day'] for e in new}
    need(set(state['completed']) <= set(checkpoint['completed']) | new_days,
         'completion without continuation attempt')
    if state['inflight']:
        need(new and state['inflight']['day'] == new[-1]['day'] and
             state['inflight']['day'] not in state['completed'] and
             state['inflight']['source']['kind'] == 'reserved_http_attempt',
             'unexpected continuation inflight')
    need(sum(e['weight'] for e in state['events']) <= p['max_total_reserved_units'],
         'continuation reservation cap exceeded')
    # Shared worker interface: this policy does not authorize any repeated day.
    return {**p, 'failed_day': None, 'completion_status': 'all_downloaded_pending_final_audit'}


def authorize_once(root, raw, pin, recovery_pin, *, now, code_commit):
    """Durable offline scope extension; an existing failure can never be cleared."""
    utc(now)
    p = validate_policy(raw, pin, root, recovery_pin)
    need(code_commit == p['execution_commit'], 'continuation execution source differs')
    worker = CacheWorker(root, recovery_pin=recovery_pin, continuation_pin=pin)
    with worker.locked(now) as (state, _, requests):
        if state.get('calendar_continuation'):
            need(state['calendar_continuation']['policy_sha256'] == pin, 'another continuation exists')
            return {'status': 'already_authorized', 'paused': state['paused'], 'network_requests': 0}
        checkpoint_raw = read_regular(root / 'state.json', MAX_STATE)
        need(digest(checkpoint_raw) == p['checkpoint_sha256'], 'checkpoint pin differs')
        checkpoint = parse_json(checkpoint_raw)
        validate_checkpoint(root, checkpoint, p, recovery_pin, now, requests)
        for day in p['allowed_missing_dates']:
            target = root / 'raw' / requests[day]['file']
            need(not target.exists() and not target.is_symlink(), 'unreceipted raw requires review')
        _preserve(root / CHECKPOINT, checkpoint_raw)
        _preserve(root / POLICY, raw)
        state['calendar_continuation'] = {'policy_sha256': pin, 'enabled_at': now.isoformat()}
        save_state(root, state)
    return {'status': 'authorized_continuation', 'network_requests': 0,
            'additional_attempts': p['max_additional_attempts']}
