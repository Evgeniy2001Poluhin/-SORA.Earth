"""One independently pinned owner-authorized retry, never calendar continuation."""
import re

from .replay_cache_worker import (
    MAX_STATE, SHA, need, read_regular, digest, parse_json, stamp, utc, save_state,
)
from .replay_cache_recovery import _preserve
from .replay_cache_continuation import CAPS
from .replay_calendar_recovery import CalendarRecoveryWorker, validate_active as parent_active

BACKUP = 'failed-transport-state.json'
AMENDMENT = 'transport-retry.json'
FINISHED = 'authorized_transport_retry_complete_stop'


def policy(raw, pin, root, recovery_pin, continuation_pin, calendar_pin):
    need(isinstance(pin, str) and SHA.fullmatch(pin) and digest(raw) == pin,
         'independent transport retry pin required')
    p = parse_json(raw)
    fields = {'schema', 'id', 'canonical_ledger', 'failed_state_sha256', 'failed_day',
              'recovery_amendment_sha256', 'continuation_sha256', 'calendar_retry_sha256',
              'execution_commit', 'allowed_missing_dates', 'max_total_attempts',
              'max_total_reserved_units', 'retry_limit', 'caps', 'scientific_changes', 'on_failure'}
    need(isinstance(p, dict) and set(p) == fields, 'transport retry fields differ')
    need(all(type(p[k]) is int for k in ('schema', 'retry_limit', 'max_total_attempts',
                                         'max_total_reserved_units')), 'integer policy required')
    need(p['schema'] == 1 and p['id'] == 'M3-P1-TRANSPORT-ONE-RETRY' and
         p['retry_limit'] == 1 and p['scientific_changes'] is False and
         p['on_failure'] == 'stop_no_more_retries', 'unreviewed retry scope')
    need(p['canonical_ledger'] == str(root.resolve()) and p['caps'] == CAPS,
         'ledger or rolling quota differs')
    for key in ('failed_state_sha256', 'recovery_amendment_sha256', 'continuation_sha256',
                'calendar_retry_sha256'):
        need(isinstance(p[key], str) and SHA.fullmatch(p[key]), 'source pin required')
    need((p['recovery_amendment_sha256'], p['continuation_sha256'], p['calendar_retry_sha256'])
         == (recovery_pin, continuation_pin, calendar_pin), 'all three parent pins required')
    need(isinstance(p['execution_commit'], str) and re.fullmatch(r'[0-9a-f]{40}', p['execution_commit']),
         'execution source required')
    need(p['allowed_missing_dates'] == [p['failed_day']], 'only the failed date is authorized')
    need(isinstance(p['failed_day'], str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', p['failed_day']),
         'explicit failed calendar date required')
    return p


def failed(root, original, p, recovery_pin, continuation_pin, calendar_pin, now, requests,
           *, check_response_absent=True):
    need(not original.get('transport_retry') and
         original['paused'] == 'request_failed:TransportFailure' and
         original.get('failure_diagnostic') == {'child_error_class': 'URLError', 'child_returncode': 2},
         'only the pinned reviewed URL transport failure is authorized')
    parent = parent_active(root, original, calendar_pin, recovery_pin, continuation_pin, now, requests)
    day = p['failed_day']
    need(original['inflight'] == {'day': day, 'source': {'kind': 'reserved_http_attempt'}} and
         day in requests and day not in original['completed'], 'failure identity differs')
    events = original['events']
    attempts = [e for e in events if e['kind'] == 'attempt']
    need(attempts and events[-1] == attempts[-1] and attempts[-1]['day'] == day and
         sum(e['day'] == day for e in attempts) == 1 and
         attempts[-1].get('calendar_continuation_sha256') == continuation_pin and
         'reserved_at' in attempts[-1] and
         stamp(attempts[-1]['reserved_at']) <= stamp(attempts[-1]['at']) <= stamp(original['last_seen']),
         'failed timing or attempt history differs')
    missing = [d for d in parent['allowed_missing_dates'] if d not in original['completed']]
    need(missing and missing[0] == day, 'failed day is not next in the reviewed calendar')
    target = root / 'raw' / requests[day]['file']
    if check_response_absent:
        need(not target.exists() and not target.is_symlink(), 'unexpected failed response artefact')
    need(p['max_total_attempts'] == len(attempts) + 1 and
         p['max_total_reserved_units'] == sum(e['weight'] for e in events) + 21,
         'only one extra attempt and 21 units are authorized')


def validate_active(root, state, pin, recovery_pin, continuation_pin, calendar_pin, now, requests):
    p = policy(read_regular(root / AMENDMENT, 100000), pin, root,
               recovery_pin, continuation_pin, calendar_pin)
    raw = read_regular(root / BACKUP, MAX_STATE)
    need(digest(raw) == p['failed_state_sha256'], 'failed transport checkpoint changed')
    original = parse_json(raw)
    # Validate the original state with the parent; the live response is now allowed
    # to exist, so absence is checked against the original before activation only.
    failed(root, original, p, recovery_pin, continuation_pin, calendar_pin, now, requests,
           check_response_absent=False)
    record = state['transport_retry']
    need(set(record) == {'amendment_sha256', 'enabled_at'} and record['amendment_sha256'] == pin and
         stamp(original['last_seen']) <= stamp(record['enabled_at']) <= now, 'authorization differs')
    for key in ('code_commit', 'plan_sha256', 'manual_recovery', 'calendar_retry'):
        need(state[key] == original[key], 'parent identity changed')
    need(state['calendar_continuation'] == {**original['calendar_continuation'], 'policy_sha256': pin},
         'effective authorization differs')
    n = len(original['events'])
    need(state['events'][:n] == original['events'] and
         all(state['completed'].get(d) == receipt for d, receipt in original['completed'].items()),
         'old quota history or completion changed')
    new = state['events'][n:]
    need(len(new) <= 1, 'second transport retry refused')
    if new:
        event = new[0]
        need(event['kind'] == 'attempt' and event['day'] == p['failed_day'] and event['weight'] == 21 and
             event.get('calendar_continuation_sha256') == continuation_pin and
             event.get('attempt_ordinal') == 2 and event.get('recovery_amendment_sha256') == pin and
             stamp(record['enabled_at']) <= stamp(event.get('reserved_at', event['at'])) <=
             stamp(event['at']) <= now, 'invalid retry reservation')
    need(set(state['completed']) <= set(original['completed']) | ({p['failed_day']} if new else set()),
         'unreserved completion')
    if p['failed_day'] in state['completed']:
        receipt = state['completed'][p['failed_day']]
        recovered = receipt['source'] == {'kind': 'reserved_http_attempt', 'recovered_without_http': True}
        need(new and (recovered or receipt['source'] == {'kind': 'reserved_http_attempt'}) and
             receipt['historical_publication'] == 'not attested', 'retry completion provenance differs')
        need(stamp(new[0]['at']) <= stamp(receipt['ledger_at']) <= now and
             (recovered or receipt['ledger_at'] == new[0]['at']), 'retry completion time differs')
    need(sum(e['kind'] == 'attempt' for e in state['events']) <= p['max_total_attempts'] and
         sum(e['weight'] for e in state['events']) <= p['max_total_reserved_units'], 'retry budget exceeded')
    if state['inflight']:
        need(new and state['inflight']['day'] == p['failed_day'] and
             p['failed_day'] not in state['completed'] and
             state['inflight']['source']['kind'] == 'reserved_http_attempt', 'unexpected inflight')
    return {**p, 'retry_pin': pin, 'completion_status': FINISHED}


class TransportRetryWorker(CalendarRecoveryWorker):
    def __init__(self, root, recovery_pin, continuation_pin, calendar_pin, transport_pin):
        super().__init__(root, recovery_pin, continuation_pin, calendar_pin)
        self.transport_pin = transport_pin

    def recovery_policy(self, state, now, requests):
        if state.get('transport_retry'):
            return validate_active(self.root, state, self.transport_pin, self.recovery_pin,
                                   self.continuation_pin, self.calendar_retry_pin, now, requests)
        return super().recovery_policy(state, now, requests)


def authorize_once(root, raw, pin, recovery_pin, continuation_pin, calendar_pin, *, now, code_commit):
    utc(now)
    p = policy(raw, pin, root, recovery_pin, continuation_pin, calendar_pin)
    need(code_commit == p['execution_commit'], 'retry execution source differs')
    worker = TransportRetryWorker(root, recovery_pin, continuation_pin, calendar_pin, pin)
    with worker.locked(now) as (state, _, requests):
        if state.get('transport_retry'):
            return {'status': 'already_authorized', 'network_requests': 0}
        original_raw = read_regular(root / 'state.json', MAX_STATE)
        need(digest(original_raw) == p['failed_state_sha256'], 'failed state pin differs')
        failed(root, parse_json(original_raw), p, recovery_pin, continuation_pin, calendar_pin, now, requests)
        _preserve(root / BACKUP, original_raw)
        _preserve(root / AMENDMENT, raw)
        state['transport_retry'] = {'amendment_sha256': pin, 'enabled_at': now.isoformat()}
        state['calendar_continuation'] = {**state['calendar_continuation'], 'policy_sha256': pin}
        state['paused'], state['inflight'] = None, None
        save_state(root, state)
    return {'status': 'authorized_transport_retry', 'network_requests': 0, 'retry_limit': 1}
