"""Continue never-attempted dates after a settled one-shot retry; no new retries."""
import re

from .replay_cache_worker import MAX_STATE, SHA, need, read_regular, digest, parse_json, stamp, utc, save_state
from .replay_cache_recovery import _preserve
from .replay_cache_continuation import CAPS
from .replay_transport_recovery import TransportRetryWorker, validate_active as parent_active

CHECKPOINT = 'completed-transport-before-continuation.json'
POLICY = 'post-retry-continuation.json'
FINISHED = 'all_downloaded_pending_final_audit'


def policy(raw, pin, root, parent_pins):
    need(isinstance(pin, str) and SHA.fullmatch(pin) and digest(raw) == pin,
         'independent post-retry continuation pin required')
    p = parse_json(raw)
    fields = {'schema', 'id', 'canonical_ledger', 'checkpoint_sha256', 'parent_pins',
              'execution_commit', 'allowed_missing_dates', 'max_additional_attempts',
              'max_total_attempts', 'max_total_reserved_units', 'retries', 'caps',
              'scientific_changes', 'on_failure'}
    need(isinstance(p, dict) and set(p) == fields, 'post-retry policy fields differ')
    need(all(type(p[k]) is int for k in ('schema', 'retries', 'max_additional_attempts',
                                         'max_total_attempts', 'max_total_reserved_units')),
         'integer continuation policy required')
    need(p['schema'] == 1 and p['id'] == 'M3-P1-POST-RETRY-CONTINUATION' and
         p['retries'] == 0 and p['scientific_changes'] is False and
         p['on_failure'] == 'stop_no_retries', 'unreviewed continuation scope')
    need(p['canonical_ledger'] == str(root.resolve()) and p['caps'] == CAPS,
         'canonical ledger or rolling quota differs')
    need(len(parent_pins) == 4 and all(isinstance(v, str) and SHA.fullmatch(v) for v in parent_pins)
         and p['parent_pins'] == list(parent_pins), 'all four parent pins required')
    need(isinstance(p['checkpoint_sha256'], str) and SHA.fullmatch(p['checkpoint_sha256']) and
         isinstance(p['execution_commit'], str) and re.fullmatch(r'[0-9a-f]{40}', p['execution_commit']),
         'checkpoint and execution source pins required')
    return p


def checkpoint_policy(root, checkpoint, p, parent_pins, now, requests):
    need(not checkpoint.get('post_retry_continuation') and checkpoint['paused'] is None and
         checkpoint['inflight'] is None, 'settled completed transport retry required')
    parent = parent_active(root, checkpoint, parent_pins[3], *parent_pins[:3], now, requests)
    need(parent['failed_day'] in checkpoint['completed'], 'one-shot retry not completed')
    missing = sorted(set(requests) - checkpoint['completed'].keys())
    attempts = [e for e in checkpoint['events'] if e['kind'] == 'attempt']
    need(missing and p['allowed_missing_dates'] == missing and
         not (set(missing) & {e['day'] for e in attempts}),
         'only exact never-attempted remaining dates allowed')
    need(p['max_additional_attempts'] == len(missing) and
         p['max_total_attempts'] == len(attempts) + len(missing) and
         p['max_total_reserved_units'] == sum(e['weight'] for e in checkpoint['events']) + 21 * len(missing),
         'remaining calendar budget differs')


def validate_active(root, state, pin, parent_pins, now, requests):
    p = policy(read_regular(root / POLICY, 100000), pin, root, parent_pins)
    raw = read_regular(root / CHECKPOINT, MAX_STATE)
    need(digest(raw) == p['checkpoint_sha256'], 'settled checkpoint changed')
    checkpoint = parse_json(raw)
    checkpoint_policy(root, checkpoint, p, parent_pins, now, requests)
    record = state['post_retry_continuation']
    need(set(record) == {'policy_sha256', 'enabled_at'} and record['policy_sha256'] == pin and
         stamp(checkpoint['last_seen']) <= stamp(record['enabled_at']) <= stamp(state['last_seen']) <= now,
         'post-retry authorization differs')
    for key in ('code_commit', 'plan_sha256', 'manual_recovery', 'calendar_retry', 'transport_retry'):
        need(state[key] == checkpoint[key], 'parent identity changed')
    need(state['calendar_continuation'] == {**checkpoint['calendar_continuation'], 'policy_sha256': pin},
         'effective continuation authorization differs')
    n = len(checkpoint['events'])
    need(state['events'][:n] == checkpoint['events'] and
         all(state['completed'].get(d) == receipt for d, receipt in checkpoint['completed'].items()),
         'parent quota history or completion changed')
    new = state['events'][n:]
    need(len(new) <= p['max_additional_attempts'] and
         [e['day'] for e in new] == p['allowed_missing_dates'][:len(new)],
         'remaining sequence differs or attempt repeated')
    previous = stamp(record['enabled_at'])
    for i, event in enumerate(new):
        need({'at', 'weight', 'kind', 'day', 'calendar_continuation_sha256'} <= set(event) <=
             {'at', 'reserved_at', 'weight', 'kind', 'day', 'calendar_continuation_sha256'},
             'new attempt cannot be labelled as a retry')
        start, end = stamp(event.get('reserved_at', event['at'])), stamp(event['at'])
        need(event['kind'] == 'attempt' and event['weight'] == 21 and
             event['calendar_continuation_sha256'] == parent_pins[1] and
             previous <= start <= end <= now, 'invalid remaining-date reservation')
        previous = end
        receipt = state['completed'].get(event['day'])
        if receipt is not None:
            recovered = receipt['source'] == {'kind': 'reserved_http_attempt', 'recovered_without_http': True}
            need((recovered or receipt['source'] == {'kind': 'reserved_http_attempt'}) and
                 receipt['historical_publication'] == 'not attested' and
                 end <= stamp(receipt['ledger_at']) <= now and
                 (recovered or receipt['ledger_at'] == event['at']), 'completion provenance or time differs')
        else:
            need(i == len(new) - 1 and state['inflight'] and
                 state['inflight']['day'] == event['day'], 'attempt without receipt or final inflight')
    need(set(state['completed']) <= set(checkpoint['completed']) | {e['day'] for e in new},
         'unreserved completion')
    if state['inflight']:
        need(new and state['inflight']['day'] == new[-1]['day'] and
             state['inflight']['day'] not in state['completed'] and
             state['inflight']['source'] == {'kind': 'reserved_http_attempt'}, 'unexpected inflight')
    need(sum(e['kind'] == 'attempt' for e in state['events']) <= p['max_total_attempts'] and
         sum(e['weight'] for e in state['events']) <= p['max_total_reserved_units'],
         'remaining calendar budget exceeded')
    return {**p, 'failed_day': None, 'completion_status': FINISHED}


class PostRetryWorker(TransportRetryWorker):
    def __init__(self, root, recovery_pin, continuation_pin, calendar_pin, transport_pin, post_pin):
        super().__init__(root, recovery_pin, continuation_pin, calendar_pin, transport_pin)
        self.parent_pins = (recovery_pin, continuation_pin, calendar_pin, transport_pin)
        self.post_pin = post_pin

    def recovery_policy(self, state, now, requests):
        if state.get('post_retry_continuation'):
            return validate_active(self.root, state, self.post_pin, self.parent_pins, now, requests)
        return super().recovery_policy(state, now, requests)


def authorize_once(root, raw, pin, *parent_pins, now, code_commit):
    """Offline transition only. Idempotent reauthorization never clears a new failure."""
    utc(now)
    p = policy(raw, pin, root, parent_pins)
    need(code_commit == p['execution_commit'], 'continuation execution source differs')
    worker = PostRetryWorker(root, *parent_pins, pin)
    with worker.locked(now) as (state, _, requests):
        if state.get('post_retry_continuation'):
            return {'status': 'already_authorized', 'paused': state['paused'], 'network_requests': 0}
        raw_checkpoint = read_regular(root / 'state.json', MAX_STATE)
        need(digest(raw_checkpoint) == p['checkpoint_sha256'], 'settled checkpoint pin differs')
        checkpoint_policy(root, parse_json(raw_checkpoint), p, parent_pins, now, requests)
        for day in p['allowed_missing_dates']:
            target = root / 'raw' / requests[day]['file']
            need(not target.exists() and not target.is_symlink(), 'unexpected unreceipted raw')
        _preserve(root / CHECKPOINT, raw_checkpoint)
        _preserve(root / POLICY, raw)
        state['post_retry_continuation'] = {'policy_sha256': pin, 'enabled_at': now.isoformat()}
        state['calendar_continuation'] = {**state['calendar_continuation'], 'policy_sha256': pin}
        save_state(root, state)
    return {'status': 'authorized_post_retry_continuation', 'network_requests': 0,
            'additional_attempts': p['max_additional_attempts'], 'retries': 0}
