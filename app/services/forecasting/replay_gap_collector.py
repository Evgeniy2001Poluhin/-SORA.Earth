"""Bounded companion acquisition; the stopped parent ledger is immutable.

Only injected transport performs IO over the network. CLI binds it to the existing
bounded public fetch and an exact clean source guard. No retries or scoring.
"""
from contextlib import contextmanager
from datetime import timedelta
import base64
import fcntl
import os
from pathlib import Path
import re
import stat

from .replay_cache_worker import (CacheWorker, MAX_RAW, MAX_STATE, TransportFailure,
                                need, stamp, utc, save_state, write_new, sync_dir, _error_body)
from .replay_cache_plan import build_plan
from .replay_availability_inventory import _read
from .replay_source_gap_contract import build_contract, canonical, digest, _pinned, CAPS

BUNDLE = {'science.json', 'inventory.json', 'contract.json', 'authorization.txt'}
PARENTS = {'manual-recovery.json', 'calendar-continuation.json', 'calendar-retry.json',
           'transport-retry.json', 'post-retry-continuation.json', 'http-probe-policy.json'}


class CompanionCollector:
    def __init__(self, parent, root, policy_raw, policy_pin, *, code_commit):
        self.parent, self.root = Path(parent), Path(root)
        self.policy_raw, self.policy_pin = policy_raw, policy_pin
        p = _pinned(policy_raw, policy_pin)
        need(set(p) == {'schema', 'id', 'parent_root', 'root', 'execution_commit',
                       'parent_state_sha256', 'parent_plan_sha256', 'bundle_pins', 'parent_policy_pins'},
             'execution policy fields differ')
        need(type(p['schema']) is int and p['schema'] == 1 and p['id'] == 'M3-P1-COMPANION112' and
             isinstance(code_commit, str) and re.fullmatch('[0-9a-f]{40}', code_commit) and
             p['execution_commit'] == code_commit, 'execution identity differs')
        need(p['parent_root'] == str(self.parent.resolve()) and p['root'] == str(self.root.resolve()) and
             self.parent.resolve() != self.root.resolve() and
             self.parent.resolve() not in self.root.resolve().parents and
             self.root.resolve() not in self.parent.resolve().parents, 'separate canonical namespace required')
        need(set(p['bundle_pins']) == BUNDLE and set(p['parent_policy_pins']) == PARENTS,
             'all metadata and six parent policy pins required')
        for pin in [p['parent_state_sha256'], p['parent_plan_sha256'],
                    *p['bundle_pins'].values(), *p['parent_policy_pins'].values()]:
            need(isinstance(pin, str) and re.fullmatch('[0-9a-f]{64}', pin), 'SHA256 required')
        self.p, self.code_commit = p, code_commit

    @contextmanager
    def parent_locks(self):
        need(self.parent.is_dir() and not self.parent.is_symlink(), 'parent directory refused')
        handles = []
        try:
            for name in ('controller.lock', 'worker.lock'):
                fd = os.open(self.parent / name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
                handles.append(fd)
                need(stat.S_ISREG(os.fstat(fd).st_mode), 'regular shared lock required')
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            for fd in reversed(handles):
                os.close(fd)

    def preflight(self, bundle, now):
        utc(now)
        need(set(bundle) == BUNDLE and all(digest(bundle[k]) == self.p['bundle_pins'][k] for k in BUNDLE),
             'bundle pin differs')
        before = _read(self.parent / 'state.json', MAX_STATE)
        state = _pinned(before, self.p['parent_state_sha256'])
        plan = _pinned(_read(self.parent / 'plan.json', MAX_STATE), self.p['parent_plan_sha256'])
        need(plan == build_plan(), 'parent request plan differs from reviewed calendar')
        for name, pin in self.p['parent_policy_pins'].items():
            _pinned(_read(self.parent / name, MAX_STATE), pin)
        rebuilt = build_contract(bundle['science.json'], bundle['inventory.json'], before,
                                 parent_pin=self.p['bundle_pins']['science.json'],
                                 inventory_pin=self.p['bundle_pins']['inventory.json'],
                                 state_pin=self.p['parent_state_sha256'],
                                 authorization_pin=self.p['bundle_pins']['authorization.txt'])
        need(canonical(rebuilt) == bundle['contract.json'], 'frozen contract differs')
        need(stamp(state['last_seen']) <= now and
             all(stamp(e['at']) <= now for e in state['events']), 'parent clock rollback')
        need(state['http_probe']['policy_sha256'] == self.p['parent_policy_pins']['http-probe-policy.json'],
             'probe policy differs')
        response = state['http_probe']['response']
        need(response['file'] == 'http-probe-response.bin', 'diagnostic path differs')
        raw = _read(self.parent / response['file'], MAX_RAW)
        need(digest(raw) == response['sha256'] and len(raw) == response['bytes'], 'probe bytes differ')
        raw_dir = self.parent / 'raw'
        need(raw_dir.is_dir() and not raw_dir.is_symlink(), 'parent raw directory refused')
        for day, receipt in state['completed'].items():
            raw = _read(raw_dir / ('ifs-' + day + '.json'), MAX_RAW)
            need(digest(raw) == receipt['sha256'] and len(raw) == receipt['bytes'], 'parent receipt differs')
        need({p.name for p in raw_dir.iterdir()} == {'ifs-'+d+'.json' for d in state['completed']},
             'parent raw entries differ')
        need(_read(self.parent / 'state.json', MAX_STATE) == before, 'parent changed during preflight')
        requests = {r['issue_date']: r for r in plan['requests']}
        allowed = rebuilt['acquisition']['allowed_dates']
        need(set(allowed) <= requests.keys(), 'new request outside pinned plan')
        return state, rebuilt, requests

    def initialize(self, bundle, *, now, guard):
        with self.parent_locks():
            parent, _, _ = self.preflight(bundle, now)
            need(not self.root.exists() and not self.root.is_symlink() and
                 self.root.parent.is_dir() and not self.root.parent.is_symlink(), 'new namespace required')
            guard()
            self.root.mkdir(mode=0o700)
            sync_dir(self.root.parent)
            (self.root / 'raw').mkdir(mode=0o700)
            (self.root / 'failures').mkdir(mode=0o700)
            sync_dir(self.root)
            for name, raw in bundle.items():
                write_new(self.root / name, raw)
            write_new(self.root / 'execution-policy.json', self.policy_raw)
            state = {'schema': 1, 'policy_sha256': self.policy_pin, 'execution_commit': self.code_commit,
                     'parent_state_sha256': self.p['parent_state_sha256'], 'enabled_at': now.isoformat(),
                     'last_seen': now.isoformat(), 'events': [], 'completed': {}, 'inflight': None, 'paused': None}
            save_state(self.root, state)
            return {'status': 'companion_initialized', 'network_requests': 0,
                    'inherited_attempts': sum(e['kind']=='attempt' for e in parent['events'])}

    def load_state(self, contract, now):
        need(self.root.is_dir() and not self.root.is_symlink(), 'companion directory refused')
        need(_read(self.root / 'execution-policy.json', MAX_STATE) == self.policy_raw, 'saved policy differs')
        raw_dir = self.root / 'raw'
        need(raw_dir.is_dir() and not raw_dir.is_symlink() and
             (self.root/'failures').is_dir() and not (self.root/'failures').is_symlink(), 'output directory refused')
        from .replay_availability_inventory import _json
        state = _json(_read(self.root / 'state.json', MAX_STATE))
        need(set(state) == {'schema', 'policy_sha256', 'execution_commit', 'parent_state_sha256',
                           'enabled_at', 'last_seen', 'events', 'completed', 'inflight', 'paused'},
             'companion state fields differ')
        need(type(state['schema']) is int and state['schema'] == 1 and
             state['policy_sha256'] == self.policy_pin and state['execution_commit'] == self.code_commit and
             state['parent_state_sha256'] == self.p['parent_state_sha256'] and
             stamp(state['enabled_at']) <= stamp(state['last_seen']) <= now, 'companion identity/clock differs')
        events, receipts, allowed = state['events'], state['completed'], contract['acquisition']['allowed_dates']
        need(isinstance(events, list) and isinstance(receipts, dict) and len(events) <= len(allowed) and
             [e['day'] for e in events] == allowed[:len(events)], 'attempt prefix differs')
        previous = stamp(state['enabled_at'])
        for index, event in enumerate(events):
            need(set(event) == {'kind', 'day', 'weight', 'reserved_at', 'at'} and
                 event['kind'] == 'attempt' and type(event['weight']) is int and event['weight'] == 21 and
                 previous <= stamp(event['reserved_at']) <= stamp(event['at']) <= stamp(state['last_seen']),
                 'reservation chronology/weight differs')
            previous = stamp(event['at'])
            receipt = receipts.get(event['day'])
            if receipt is None:
                need(index == len(events)-1 and state['inflight'] and
                     state['inflight']['day'] == event['day'], 'missing receipt without final inflight')
            else:
                need(set(receipt) == {'sha256', 'bytes', 'at', 'policy_sha256', 'historical_publication'} and
                     receipt['at'] == event['at'] and receipt['policy_sha256'] == self.policy_pin and
                     receipt['historical_publication'] == 'not attested' and
                     type(receipt['bytes']) is int, 'completion provenance differs')
                raw = _read(raw_dir / ('ifs-'+event['day']+'.json'), MAX_RAW)
                need(len(raw) == receipt['bytes'] and digest(raw) == receipt['sha256'], 'new raw receipt differs')
        need(set(receipts) <= {e['day'] for e in events}, 'unreserved completion')
        inflight = state['inflight']
        if inflight:
            need(isinstance(inflight, dict) and set(inflight) in ({'day'}, {'day','sha256','bytes'}) and
                 events and inflight['day'] == events[-1]['day'] and inflight['day'] not in receipts,
                 'inflight differs')
        need(state['paused'] is None or isinstance(state['paused'], str) and bool(state['paused']), 'invalid pause')
        expected = {'ifs-'+d+'.json' for d in receipts}
        if inflight:
            path = raw_dir/('ifs-'+inflight['day']+'.json')
            if path.exists() or path.is_symlink():
                raw = _read(path, MAX_RAW)
                need(set(inflight) == {'day','sha256','bytes'} and
                     len(raw) == inflight['bytes'] and digest(raw) == inflight['sha256'], 'orphan response differs')
                expected.add(path.name)
        need({p.name for p in raw_dir.iterdir()} == expected, 'unexpected companion raw entry')
        return state

    def step(self, *, fetch, clock, guard):
        with self.parent_locks():
            now = utc(clock())
            need(self.root.is_dir() and not self.root.is_symlink(), 'companion directory refused')
            bundle = {k: _read(self.root/k, MAX_STATE) for k in BUNDLE}
            parent, contract, requests = self.preflight(bundle, now)
            state = self.load_state(contract, now)
            if state['paused']:
                return {'status': 'paused', 'reason': state['paused'], 'network_requests': 0}
            if state['inflight']:
                state['paused'] = 'uncertain_attempt_requires_review_no_retry'
                save_state(self.root, state)
                return {'status':'paused', 'reason':state['paused'], 'network_requests':0}
            acquisition = contract['acquisition']
            if len(state['completed']) == acquisition['max_new_requests']:
                return {'status': 'all112_received_pending606_audit', 'network_requests':0}
            # Preflight hashes may take time. Reserve against a fresh clock,
            # rather than expiring an attempt early due to a stale timestamp.
            fresh = utc(clock())
            need(fresh >= now, 'clock rollback during preflight')
            now = fresh
            combined = {'events': parent['events'] + state['events']}
            need(sum(e['kind']=='attempt' for e in combined['events']) + 1 <= acquisition['max_combined_attempts'] and
                 sum(e['weight'] for e in combined['events']) + 21 <= acquisition['max_combined_reserved_units'],
                 'combined budget exhausted')
            allowed_at = CacheWorker(self.root).wait_until(combined, now)
            if allowed_at > now:
                return {'status':'quota_wait', 'not_before':allowed_at.isoformat(), 'network_requests':0}
            day = acquisition['allowed_dates'][len(state['events'])]
            guard()
            state['events'].append({'kind':'attempt', 'day':day, 'weight':21,
                                    'reserved_at':now.isoformat(), 'at':now.isoformat()})
            state['inflight'] = {'day':day}
            state['last_seen'] = now.isoformat()
            save_state(self.root, state)  # Every uncertain attempt remains charged.
            try:
                guard()
                raw = fetch(requests[day]['url'], 40, MAX_RAW)
                need(isinstance(raw, bytes) and len(raw) <= MAX_RAW, 'response byte budget differs')
                state['inflight'] = {'day':day, 'sha256':digest(raw), 'bytes':len(raw)}
                save_state(self.root, state)  # Retain bounded bytes even if the clock fails next.
                write_new(self.root/'raw'/('ifs-'+day+'.json'), raw)
                finished = utc(clock())
                need(finished >= now, 'clock rollback after request')
                state['events'][-1]['at'] = finished.isoformat()
                state['last_seen'] = finished.isoformat()
                save_state(self.root, state)
                CacheWorker(self.root).validate(raw, requests[day])
                state['completed'][day] = {'sha256':digest(raw), 'bytes':len(raw), 'at':finished.isoformat(),
                                           'policy_sha256':self.policy_pin, 'historical_publication':'not attested'}
                state['inflight'] = None
                save_state(self.root, state)
                return {'status':'received', 'day':day, 'completed_new':len(state['completed']),
                        'network_attempt_budget_consumed':1, 'automatic_retries':0}
            except Exception as exc:
                # BaseException leaves the durable reservation for review, never
                # authorizes retry. Output failures also leave a charged stop.
                state['paused'] = 'request_failed:'+type(exc).__name__
                code = exc.code if isinstance(exc, TransportFailure) else None
                if type(code) is int and 100 <= code <= 599:
                    state['paused'] += ':'+str(code)
                save_state(self.root, state)
                if isinstance(exc, TransportFailure):
                    meta = _error_body(exc.http_error_body)
                    info = {'http_status':code if type(code) is int and 100 <= code <= 599 else None,
                            'error_class':exc.child_error_class, 'returncode':exc.child_returncode,
                            'prefix':{k:meta[k] for k in ('bytes','sha256','truncated')} if meta else None}
                    # Diagnostic writes cannot undo the already durable pause.
                    write_new(self.root/'failures'/(day+'.http-error.json'), canonical(info))
                    if meta is not None:
                        body = base64.b64decode(meta['base64'], validate=True)
                        write_new(self.root/'failures'/(day+'.http-error-prefix.bin'), body)
                return {'status':'paused', 'reason':state['paused'], 'day':day,
                        'network_attempt_budget_consumed':1, 'automatic_retries':0}
