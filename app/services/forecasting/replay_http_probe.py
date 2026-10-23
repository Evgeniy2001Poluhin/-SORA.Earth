"""One explicitly pinned diagnostic request. Never resumes collection or a retry loop."""
import base64
import fcntl
import os
import re

from .replay_cache_worker import (
    SHA, MAX_STATE, MAX_RAW, TransportFailure, need, utc, stamp, read_regular,
    digest, parse_json, canonical, save_state, write_new,
)
from .replay_cache_recovery import _preserve
from .replay_cache_continuation import CAPS
from .replay_post_retry_continuation import PostRetryWorker

CHECKPOINT = 'failed-state-before-http-probe.json'
POLICY = 'http-probe-policy.json'
RESPONSE = 'http-probe-response.bin'


def policy(raw, pin, root, parents, code):
    need(isinstance(pin, str) and SHA.fullmatch(pin) and digest(raw) == pin, 'independent probe pin required')
    p = parse_json(raw)
    need(isinstance(p, dict) and set(p) == {'schema', 'id', 'canonical_ledger', 'failed_state_sha256',
         'failed_day', 'parent_pins', 'execution_commit', 'max_total_attempts', 'max_total_reserved_units',
         'max_requests', 'caps', 'scientific_changes'}, 'probe policy fields differ')
    need(type(p['schema']) is int and p['schema'] == 1 and p['id'] == 'M3-P1-ONE-HTTP-PROBE' and
         type(p['max_requests']) is int and p['max_requests'] == 1 and
         p['scientific_changes'] is False, 'only one diagnostic request authorized')
    need(p['canonical_ledger'] == str(root.resolve()) and p['caps'] == CAPS, 'ledger/quota differs')
    need(len(parents) == 5 and all(isinstance(v, str) and SHA.fullmatch(v) for v in parents) and
         p['parent_pins'] == list(parents), 'all five parent pins required')
    need(isinstance(p['failed_state_sha256'], str) and SHA.fullmatch(p['failed_state_sha256']) and
         isinstance(code, str) and re.fullmatch(r'[0-9a-f]{40}', code) and p['execution_commit'] == code,
         'checkpoint/execution source differs')
    need(type(p['max_total_attempts']) is int and type(p['max_total_reserved_units']) is int,
         'integer ceilings required')
    return p


def probe_once(root, raw, pin, parents, *, code_commit, fetch, clock, guard):
    """Charge BEFORE transport; leave original pause/inflight/completions untouched.

    A crash after reservation consumes the sole permission, even if it happened
    before the GET. No second invocation can issue another request.
    """
    p = policy(raw, pin, root, parents, code_commit)
    fd = os.open(root / 'controller.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = read_regular(root / 'state.json', MAX_STATE)
        need(not parse_json(before).get('http_probe'), 'probe already reserved; no repeat')
        need(digest(before) == p['failed_state_sha256'], 'failed state pin differs')
        w = PostRetryWorker(root, *parents)
        with w.locked(utc(clock())) as (state, _, requests):
            need(read_regular(root / 'state.json', MAX_STATE) == before, 'ledger changed before probe')
            day = p['failed_day']
            need(state['paused'] == 'request_failed:TransportFailure:400' and
                 state.get('failure_diagnostic') == {'child_error_class': 'HTTPError', 'child_returncode': 2} and
                 state['inflight'] == {'day': day, 'source': {'kind': 'reserved_http_attempt'}} and
                 day in requests and day not in state['completed'], 'only pinned HTTP400 is reviewed')
            attempts = [e for e in state['events'] if e['kind'] == 'attempt']
            need(attempts and state['events'][-1] == attempts[-1] and attempts[-1]['day'] == day and
                 sum(e['day'] == day for e in attempts) == 1, 'failed attempt identity differs')
            need(p['max_total_attempts'] == len(attempts) + 1 and
                 p['max_total_reserved_units'] == sum(e['weight'] for e in state['events']) + 21,
                 'only one charged request allowed')
            target = root / 'raw' / requests[day]['file']
            need(not target.exists() and not target.is_symlink() and
                 not (root / RESPONSE).exists() and not (root / RESPONSE).is_symlink(), 'unexpected response')
            now = utc(clock())
            need(now >= stamp(state['last_seen']), 'clock rollback before reservation')
            allowed = w.wait_until(state, now)
            if allowed > now:
                return {'status': 'quota_wait', 'not_before': allowed.isoformat(), 'network_requests': 0}
            guard()
            _preserve(root / CHECKPOINT, before)
            _preserve(root / POLICY, raw)
            # The changed effective pin makes every old collector refuse this
            # diagnostic ledger. The parent marker remains immutable in CHECKPOINT.
            state['calendar_continuation'] = {**state['calendar_continuation'], 'policy_sha256': pin}
            state['events'].append({'kind': 'attempt', 'day': day, 'weight': 21, 'at': now.isoformat(),
                                    'reserved_at': now.isoformat(), 'attempt_ordinal': 2,
                                    'recovery_amendment_sha256': pin,
                                    'calendar_continuation_sha256': parents[1]})
            record = {'policy_sha256': pin, 'reserved_at': now.isoformat(), 'outcome': None, 'response': None}
            state['http_probe'] = record
            state['last_seen'] = now.isoformat()
            save_state(root, state)
            response = None
            try:
                guard()  # After reservation; source drift never leads to an HTTP call.
                response = fetch(requests[day]['url'], 40, MAX_RAW)
                need(isinstance(response, bytes) and len(response) <= MAX_RAW, 'response budget/type')
                outcome = {'kind': 'http_response', 'http_status': 200}
                try:
                    w.validate(response, requests[day])
                    outcome['valid_weather_grid'] = True
                except Exception:
                    # Received bounded bytes remain diagnostic evidence even if
                    # the optional weather parser itself fails unexpectedly.
                    outcome['valid_weather_grid'] = False
            except TransportFailure as exc:
                outcome = {'kind': 'transport_failure', 'http_status': exc.code,
                           'error_class': exc.child_error_class, 'returncode': exc.child_returncode}
                if exc.http_error_body is not None:
                    response = base64.b64decode(exc.http_error_body['base64'], validate=True)
                    outcome['body_truncated'] = exc.http_error_body['truncated']
            except Exception:
                outcome = {'kind': 'uncertain_failure_stop'}
                response = None
            # BaseException intentionally leaves the already-durable reservation
            # for review. Neither it nor an output/save error can authorize retry.
            finished = utc(clock())
            need(finished >= now, 'clock rollback after request; reserved probe remains consumed')
            state['events'][-1]['at'] = finished.isoformat()
            state['last_seen'] = finished.isoformat()
            record['outcome'] = outcome
            if response is not None:
                record['response'] = {'file': RESPONSE, 'sha256': digest(response), 'bytes': len(response)}
            save_state(root, state)  # Pin diagnostic bytes before exclusive write.
            if response is not None:
                write_new(root / RESPONSE, response)
            return {'status': 'diagnostic_finished_stop', 'day': day, 'outcome': outcome,
                    'response': record['response'], 'network_attempt_budget_consumed': 1,
                    'automatic_retries': 0, 'collection_resumed': False}
    finally:
        os.close(fd)
