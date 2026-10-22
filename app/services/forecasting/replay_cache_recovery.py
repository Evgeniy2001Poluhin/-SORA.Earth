"""Explicit one-time acquisition amendment, never an automatic retry policy.

Original failure bytes/events are retained. An external amendment pin is required
on every resumed operation. Same-user hostile file replacement is out of scope.
"""
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
import re

from .replay_cache_worker import (
    CacheWorker, MAX_STATE, SHA, need, read_regular, digest, parse_json,
    stamp, utc, write_new, save_state,
)

BACKUP = 'failed-state-before-recovery.json'
AMENDMENT = 'manual-recovery.json'


def stage_dates():
    return {str(date(2024, 6, 27) + timedelta(days=i)) for i in range(180)}


def validate_amendment(raw, pin, root):
    need(isinstance(pin, str) and SHA.fullmatch(pin) and digest(raw) == pin,
         'independent recovery amendment pin required')
    p = parse_json(raw)
    need(isinstance(p, dict) and all(type(p.get(k)) is int for k in
         ('schema', 'retry_limit', 'original_attempts', 'max_total_attempts', 'max_total_reserved_units')),
         'integer recovery policy required')
    need(p['schema'] == 1 and p['id'] == 'M3-P1-ONE-RECOVERY-2026-10-22' and
         p['retry_limit'] == 1 and p['failed_day'] == '2024-11-25', 'unreviewed recovery scope')
    need(p['canonical_ledger'] == str(root.resolve()), 'recovery ledger path differs')
    need(SHA.fullmatch(p['failed_state_sha256']) and
         re.fullmatch(r'[0-9a-f]{40}', p['execution_commit']), 'recovery pins required')
    need(p['caps'] == {'rolling60s': 400, 'rolling3600s': 4000,
                       'rolling86400s': 7000, 'spacing_seconds_after_response': 4},
         'recovery quota policy differs')
    need(p['scientific_changes'] is False and p['on_failure'] == 'stop_no_more_retries',
         'recovery cannot change science or permit more retries')
    return p


def validate_original(original, p, requests):
    need(original['paused'] == 'request_failed:TransportFailure' and
         original['inflight'] == {'day': p['failed_day'], 'source': {'kind': 'reserved_http_attempt'}},
         'only reviewed failed transport without response is recoverable')
    events = original['events']
    attempts = [e for e in events if e['kind'] == 'attempt']
    need(attempts and events[-1] == attempts[-1] and attempts[-1]['day'] == p['failed_day'] and
         len(attempts) == len({e['day'] for e in attempts}), 'original attempt history differs')
    need(p['failed_day'] in requests and p['failed_day'] not in original['completed'],
         'failed date is not missing')
    missing = sorted(stage_dates() - original['completed'].keys())
    need(p['allowed_missing_dates'] == missing and missing and missing[0] == p['failed_day'],
         'recovery date set differs')
    need(p['original_attempts'] == len(attempts) and
         p['max_total_attempts'] == len(attempts) + len(missing) and
         p['max_total_reserved_units'] == sum(e['weight'] for e in events) + 21 * len(missing),
         'recovery total budget differs')


def validate_active(root: Path, state, pin, now, requests):
    """Called under the existing exclusive cache lock, before accepting repeats."""
    p = validate_amendment(read_regular(root / AMENDMENT, 100000), pin, root)
    raw = read_regular(root / BACKUP, MAX_STATE)
    need(digest(raw) == p['failed_state_sha256'], 'original failure backup changed')
    original = parse_json(raw)
    validate_original(original, p, requests)
    record = state['manual_recovery']
    need(record['amendment_sha256'] == pin and
         stamp(original['last_seen']) <= stamp(record['enabled_at']) <= now,
         'recovery authorization differs')
    need(state['code_commit'] == original['code_commit'] and
         state['plan_sha256'] == original['plan_sha256'], 'initial source/plan identity changed')
    need(all(state['completed'].get(d) == receipt for d, receipt in original['completed'].items()),
         'original completion changed')
    n = len(original['events'])
    need(state['events'][:n] == original['events'], 'original quota events changed')
    new = state['events'][n:]
    need(set(state['completed']) <= set(original['completed']) | set(p['allowed_missing_dates']),
         'completion outside recovery scope')
    need(len(new) <= len(p['allowed_missing_dates']), 'recovery attempt budget exceeded')
    need([e['day'] for e in new] == p['allowed_missing_dates'][:len(new)],
         'recovery sequence differs or repeated retry')
    for i, e in enumerate(new):
        need(e['kind'] == 'attempt' and e['weight'] == 21 and
             stamp(e['at']) >= stamp(record['enabled_at']), 'invalid recovery reservation')
        if i == 0:
            need(e.get('attempt_ordinal') == 2 and e.get('recovery_amendment_sha256') == pin,
                 'controlled retry lacks amendment/ordinal')
    counts = Counter(e['day'] for e in state['events'] if e['kind'] == 'attempt')
    need(all(count == (2 if day == p['failed_day'] and new else 1)
             for day, count in counts.items()), 'duplicate outside one authorized retry')
    need(sum(e['weight'] for e in state['events']) <= p['max_total_reserved_units'],
         'recovery total reservation cap exceeded')
    return p


def _preserve(path, raw):
    if path.exists() or path.is_symlink():
        need(read_regular(path, MAX_STATE) == raw, 'conflicting recovery artifact')
    else:
        write_new(path, raw)


def authorize_once(root, raw, pin, *, now, code_commit):
    """Offline durable authorization only; idempotent, never clears a later error."""
    utc(now)
    p = validate_amendment(raw, pin, root)
    need(code_commit == p['execution_commit'], 'recovery execution commit differs')
    worker = CacheWorker(root, recovery_pin=pin)
    with worker.locked(now) as (state, _, requests):
        if state.get('manual_recovery'):
            need(state['manual_recovery']['amendment_sha256'] == pin, 'another recovery already exists')
            return {'status': 'already_authorized', 'paused': state['paused'], 'network_requests': 0}
        original_raw = read_regular(root / 'state.json', MAX_STATE)
        need(digest(original_raw) == p['failed_state_sha256'], 'failed state pin differs')
        original = parse_json(original_raw)
        validate_original(original, p, requests)
        target = root / 'raw' / requests[p['failed_day']]['file']
        need(not target.exists() and not target.is_symlink(), 'response exists; review local recovery instead')
        _preserve(root / BACKUP, original_raw)
        _preserve(root / AMENDMENT, raw)
        state['manual_recovery'] = {'amendment_sha256': pin, 'enabled_at': now.isoformat()}
        # Original paused/inflight stay in the byte-pinned immutable backup.
        state['paused'], state['inflight'] = None, None
        save_state(root, state)
    return {'status': 'authorized_once', 'network_requests': 0, 'failed_attempt_preserved': True}
