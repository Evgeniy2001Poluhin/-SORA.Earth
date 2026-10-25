"""Source-coverage census over the 111 issue dates left after the parent and companion ledgers.

The parent (P) and companion (G) ledgers are read-only ancestors. Their complete
expense histories are charged to every rolling window; nothing resets at UTC midnight.
The only network path is the injected transport; the CLI binds the existing bounded
fetch. Each step is serialised by the parent locks, reserves durably before the
request, checks the source guard again before transport, and never retries or sleeps.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import base64
import fcntl
import os
import re
import stat

from app.services.environmental.regions import OPENMETEO_POINTS_V1

from .replay_availability_inventory import _read
from .replay_cache_plan import HORIZON, _request, build_plan, eligible, origins
from .replay_cache_worker import (CacheRefusal, CacheWorker, MAX_RAW, MAX_STATE, TransportFailure,
                                  digest, need, save_state, stamp, sync_dir, utc, write_new)
from .replay_gap_collector import PARENTS
from .replay_p0 import ReplayRefusal, parse_json
from .replay_source_gap_contract import CAPS, canonical

CENSUS_ID = 'M3-SOURCE-COVERAGE-CENSUS111-V1'
MODEL = 'ecmwf_ifs'
ENDPOINT = 'single-runs-api.open-meteo.com'
UNITS = 21
GET_TIMEOUT = 40
MAX_ERROR_BODY = 1024
PARENT_RECEIPTS = 494
RECORDED_GAP = '2025-08-05'
HISTORICAL_GAP = '2025-08-06'
EXCLUDED = [RECORDED_GAP, HISTORICAL_GAP]
FIRST_ISSUE = '2024-04-01'
LAST_ISSUE = '2025-11-28'
ISSUE_DATES = 607
OBSERVATION_MIN_HOURS = 19
COMPLETE = 'census_complete_pending_scientific_decision'
NOT_COMPUTED = 'pinned observed-actual archive is not bound by census v1'

BASELINE = {'attempts': 486, 'units': 12678}
CENSUS_LIMIT = {'attempts': 111, 'units': 2331}
COMBINED_LIMIT = {'attempts': 597, 'units': 15009}
BUDGET = {'baseline': BASELINE, 'census_limit': CENSUS_LIMIT, 'combined_limit': COMBINED_LIMIT,
          'units_per_attempt': UNITS, 'rolling': CAPS}
TERMINAL_ERROR_RULE = {
    'http_status': 400, 'error_class': 'HTTPError',
    'body': 'complete, not truncated, strict JSON without duplicate keys',
    'payload_keys': ['error', 'reason'], 'error': True,
    'reason': 'The requested model run is not available. Model: {model}, run: {day}T{hour}:00Z',
    'model_and_hour_from': 'pinned request', 'day_from': 'reservation',
    'substring_match': False, 'other_outcomes': 'global STOP',
    'meaning': 'recorded source response for one run; not proof of permanent absence',
}
TRANSPORT = {
    'method': 'GET', 'requests_per_step': 1, 'automatic_retries': 0,
    'timeout_seconds': GET_TIMEOUT, 'hard_deadline_seconds': 45,
    'max_response_bytes': MAX_RAW, 'max_error_body_bytes': MAX_ERROR_BODY,
    'endpoint': 'https://' + ENDPOINT + '/v1/forecast', 'url_source': 'pinned P/plan.json request',
    'alternate_endpoints': [], 'sleep_inside_worker': False,
}
PUBLICATION_SCOPE = {
    'calendar_issue_dates': ISSUE_DATES,
    'calendar_statuses': ['fetched_ok', 'recorded_run_unavailable', 'unrequested', 'reserved_or_stop'],
    'valid_run_count': True,
    'pair_counts': 'per origin x point x lead, zeros and incomplete cells included, or not_computed with reason',
    'excluded': ['weather values', 'forecasts', 'errors', 'quality metrics', 'training', 'scientific export'],
}

BUNDLE_FILES = frozenset({'contract.json', 'inherited-manifest.json', 'authorization.txt'})
PINNED_BUNDLE = frozenset({'contract.json', 'inherited-manifest.json'})
TOP_LEVEL = frozenset({'execution-policy.json', 'authorization.txt', 'contract.json',
                       'inherited-manifest.json', 'state.json', 'raw', 'failures'})
POLICY_FIELDS = frozenset({'schema', 'id', 'execution_commit', 'root', 'ancestor_pins', 'bundle_pins',
                           'allowed_dates', 'excluded_dates', 'budget', 'terminal_error_rule',
                           'transport', 'publication_scope', 'authorization_sha256'})
STATE_FIELDS = frozenset({'schema', 'policy_sha256', 'execution_commit', 'enabled_at', 'last_seen',
                          'events', 'terminal', 'inflight', 'paused'})
EVENT_FIELDS = frozenset({'kind', 'day', 'weight', 'reserved_at', 'at', 'request_sha256'})
INFLIGHT_FIELDS = frozenset({'day', 'request_sha256', 'response_sha256', 'response_bytes',
                             'body_sha256', 'body_bytes'})
FETCHED_FIELDS = frozenset({'status', 'sha256', 'bytes', 'at', 'policy_sha256'})
UNAVAILABLE_FIELDS = FETCHED_FIELDS | {'http_status'}
SHA = re.compile(r'[0-9a-f]{64}\Z')
COMMIT = re.compile(r'[0-9a-f]{40}\Z')
DAY = re.compile(r'\d{4}-\d{2}-\d{2}\Z')


def _sha(value):
    need(isinstance(value, str) and SHA.match(value) is not None, 'SHA256 pin required')
    return value


def _same(left, right):
    return canonical(left) == canonical(right)


def _load(raw):
    try:
        return parse_json(raw)
    except ReplayRefusal as exc:
        raise CacheRefusal('invalid or ambiguous JSON') from exc


def _file(path, limit=MAX_STATE):
    return _read(path, limit)


def _need_dir(path):
    need(path.is_dir() and not path.is_symlink(), 'directory refused')


def _dates(value):
    need(isinstance(value, list) and all(isinstance(d, str) and DAY.match(d) for d in value),
         'date list differs')
    for day in value:
        date.fromisoformat(day)
    return value


def _request_sha(request):
    return digest(request['url'].encode('utf-8'))


@lru_cache(maxsize=1)
def reviewed_plan():
    """The reviewed P request plan. Callers must treat the result as read-only."""
    return build_plan()


def source_calendar(gap_contract_raw):
    """Exact ordered G acquisition calendar without the historical 2025-08-06 run."""
    contract = _load(gap_contract_raw)
    need(type(contract) is dict and type(contract.get('acquisition')) is dict, 'gap contract shape differs')
    source = _dates(contract['acquisition'].get('allowed_dates'))
    need(len(source) == 112 and source == sorted(set(source)) and HISTORICAL_GAP in source and
         RECORDED_GAP not in source, 'source calendar differs')
    allowed = [day for day in source if day != HISTORICAL_GAP]
    need(len(allowed) == 111 and len(set(allowed)) == 111 and allowed == sorted(allowed) and
         not set(allowed) & set(EXCLUDED), 'census calendar differs')
    return allowed


def inherited_manifest(parent, gap, completed_days):
    """Every inherited byte the census relies on, read from disk, in a fixed order."""
    parent, gap = Path(parent), Path(gap)
    rows = [('P', parent, 'raw/ifs-' + day + '.json') for day in sorted(completed_days)]
    rows += [('P', parent, 'http-probe-response.bin'),
             ('G', gap, 'failures/' + HISTORICAL_GAP + '.http-error.json'),
             ('G', gap, 'failures/' + HISTORICAL_GAP + '.http-error-prefix.bin')]
    entries = []
    for label, base, relative in rows:
        raw = _file(base / relative, MAX_RAW)
        entries.append({'root': label, 'path': relative, 'bytes': len(raw), 'sha256': digest(raw)})
    return {'schema': 1, 'entries': entries}


def census_contract(gap_contract_raw, *, manifest_sha256):
    return {'schema': 1, 'id': CENSUS_ID,
            'source_gap_contract_sha256': digest(gap_contract_raw), 'source_allowed_dates': 112,
            'excluded_dates': EXCLUDED, 'allowed_dates': source_calendar(gap_contract_raw),
            'calendar_first': FIRST_ISSUE, 'calendar_last': LAST_ISSUE, 'issue_dates': ISSUE_DATES,
            'inherited_manifest_sha256': _sha(manifest_sha256)}


def classify_unavailable(exc, *, day, request):
    """Complete body of the one recorded unavailable answer for this reservation, or None.

    Only HTTP 400 from HTTPError with a complete, strict JSON body whose error is true
    and whose reason is exactly the pinned model, date and hour qualifies.
    """
    if not isinstance(exc, TransportFailure) or exc.code != 400 or exc.child_error_class != 'HTTPError':
        return None
    meta = exc.http_error_body
    if meta is None or meta.get('truncated') is not False:
        return None
    body = base64.b64decode(meta['base64'], validate=True)
    if len(body) != meta['bytes'] or digest(body) != meta['sha256']:
        return None
    try:
        payload = parse_json(body)
    except ReplayRefusal:
        return None
    model, hour = _model_hour(request)
    expected = 'The requested model run is not available. Model: %s, run: %sT%s:00Z' % (model, day, hour)
    if type(payload) is not dict or set(payload) != {'reason', 'error'}:
        return None
    if payload['error'] is not True or payload['reason'] != expected:
        return None
    return body


def _model_hour(request):
    query = parse_qs(urlsplit(request['url']).query)
    models, runs = query.get('models'), query.get('run')
    need(models is not None and len(models) == 1 and runs is not None and len(runs) == 1 and
         re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}', runs[0]) is not None,
         'pinned request model or run differs')
    return models[0], runs[0][11:13]


def _check_endpoint(url):
    parts = urlsplit(url)
    need(parts.scheme == 'https' and parts.netloc == ENDPOINT and parts.path == '/v1/forecast' and
         not parts.fragment, 'endpoint differs from pinned plan')


def _finished(clock, now):
    """Completion time, or None when the clock fails or runs backwards."""
    try:
        finished = utc(clock())
    except Exception:
        return None
    return finished if finished >= now else None


def _close(state, finished):
    state['events'][-1]['at'] = finished.isoformat()
    state['last_seen'] = finished.isoformat()


def _failure_reason(exc):
    if isinstance(exc, TransportFailure):
        code = exc.code if type(exc.code) is int else None
        return 'request_failed:TransportFailure:%s:%s' % (code, exc.child_error_class)
    return 'request_failed:' + type(exc).__name__


@dataclass(frozen=True)
class Ancestors:
    allowed: tuple
    requests: dict
    parent_events: list
    gap_events: list
    parent_completed: dict
    gap_contract: bytes


def _same_pair(a, b):
    return a != b and a not in b.parents and b not in a.parents


class CensusCollector:
    def __init__(self, parent, gap, root, policy_raw, policy_pin, *, code_commit):
        self.parent, self.gap, self.root = Path(parent), Path(gap), Path(root)
        self.policy_raw, self.policy_pin, self.code_commit = policy_raw, policy_pin, code_commit
        need(isinstance(policy_raw, bytes) and digest(policy_raw) == _sha(policy_pin),
             'independent policy pin differs')
        self.p = _validate_policy(_load(policy_raw), parent=self.parent, gap=self.gap, root=self.root,
                                  code_commit=code_commit)

    @contextmanager
    def locks(self):
        """Parent controller lock, then parent worker lock, held until the caller returns."""
        _need_dir(self.parent)
        handles = []
        try:
            for name in ('controller.lock', 'worker.lock'):
                fd = os.open(self.parent / name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
                handles.append(fd)
                need(stat.S_ISREG(os.fstat(fd).st_mode), 'regular shared lock required')
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise CacheRefusal('parent lock busy') from exc
            yield
        finally:
            for fd in reversed(handles):
                os.close(fd)

    def _ancestors(self, bundle, now):
        pins = self.p['ancestor_pins']
        parent_pins, gap_pins, evidence = pins['parent'], pins['gap'], pins['unavailable_evidence']
        pstate_raw = _file(self.parent / 'state.json')
        need(digest(pstate_raw) == parent_pins['state_sha256'], 'parent state pin differs')
        pstate = _load(pstate_raw)
        need(type(pstate) is dict and isinstance(pstate.get('completed'), dict) and
             isinstance(pstate.get('events'), list) and 'last_seen' in pstate, 'parent state schema differs')
        plan_raw = _file(self.parent / 'plan.json')
        need(digest(plan_raw) == parent_pins['plan_sha256'], 'parent plan pin differs')
        need(_load(plan_raw) == reviewed_plan(), 'parent request plan differs from reviewed calendar')
        for name, pin in parent_pins['policy_sha256'].items():
            need(digest(_file(self.parent / name)) == pin, 'parent policy pin differs')
        need(digest(_file(self.parent / 'http-probe-response.bin')) == evidence['parent'],
             'parent unavailable evidence differs')
        completed = pstate['completed']
        need(len(completed) == PARENT_RECEIPTS, 'parent receipt count differs')
        raw_dir = self.parent / 'raw'
        _need_dir(raw_dir)
        need({p.name for p in raw_dir.iterdir()} == {'ifs-' + d + '.json' for d in completed},
             'parent raw entries differ')
        for day, receipt in completed.items():
            raw = _file(raw_dir / ('ifs-' + day + '.json'), MAX_RAW)
            need(type(receipt) is dict and digest(raw) == receipt.get('sha256') and
                 len(raw) == receipt.get('bytes'), 'parent receipt differs')
        gstate_raw = _file(self.gap / 'state.json')
        need(digest(gstate_raw) == gap_pins['state_sha256'], 'gap state pin differs')
        gstate = _load(gstate_raw)
        need(type(gstate) is dict and isinstance(gstate.get('events'), list) and gstate.get('completed') == {} and
             'last_seen' in gstate, 'gap state schema differs')
        gap_contract = _file(self.gap / 'contract.json')
        need(digest(gap_contract) == gap_pins['contract_sha256'], 'gap contract pin differs')
        need(digest(_file(self.gap / 'execution-policy.json')) == gap_pins['execution_policy_sha256'],
             'gap execution policy pin differs')
        for name, pin in gap_pins['metadata_sha256'].items():
            need(digest(_file(self.gap / name)) == pin, 'gap metadata pin differs')
        _need_dir(self.gap / 'raw')
        need(not list((self.gap / 'raw').iterdir()), 'gap raw directory must be empty')
        need(digest(_file(self.gap / 'failures' / (HISTORICAL_GAP + '.http-error-prefix.bin'))) ==
             evidence['gap'], 'gap unavailable evidence differs')
        allowed = source_calendar(gap_contract)
        need(_same(allowed, self.p['allowed_dates']), 'census calendar differs from policy')
        requests = {r['issue_date']: r for r in reviewed_plan()['requests']}
        need(set(allowed) <= requests.keys(), 'census date outside pinned plan')
        need(canonical(inherited_manifest(self.parent, self.gap, completed)) == bundle['inherited-manifest.json'],
             'inherited manifest differs from disk')
        p_events, g_events = pstate['events'], gstate['events']
        for event in [*p_events, *g_events]:
            need(type(event) is dict and event.get('kind') in ('attempt', 'prior_allowance') and
                 type(event.get('weight')) is int and event['weight'] > 0 and 'at' in event and
                 stamp(event['at']) <= now, 'ancestor expense event differs')
        attempts = sum(e['kind'] == 'attempt' for e in [*p_events, *g_events])
        units = sum(e['weight'] for e in [*p_events, *g_events])
        need(attempts == BASELINE['attempts'] and units == BASELINE['units'],
             'frozen source baseline differs')
        need(stamp(pstate['last_seen']) <= now and stamp(gstate['last_seen']) <= now,
             'ancestor clock rollback')
        return Ancestors(allowed=tuple(allowed), requests=requests, parent_events=p_events,
                         gap_events=g_events, parent_completed=completed, gap_contract=gap_contract)

    def _preflight(self, bundle, now):
        utc(now)
        need(set(bundle) == BUNDLE_FILES and all(isinstance(v, bytes) for v in bundle.values()),
             'bundle members differ')
        need(digest(bundle['contract.json']) == self.p['bundle_pins']['contract.json'], 'bundle contract pin differs')
        need(digest(bundle['inherited-manifest.json']) == self.p['bundle_pins']['inherited-manifest.json'] ==
             self.p['ancestor_pins']['inherited_manifest_sha256'], 'bundle manifest pin differs')
        need(digest(bundle['authorization.txt']) == self.p['authorization_sha256'], 'authorization pin differs')
        ancestors = self._ancestors(bundle, now)
        contract = census_contract(ancestors.gap_contract,
                                   manifest_sha256=digest(bundle['inherited-manifest.json']))
        need(canonical(contract) == bundle['contract.json'], 'census contract differs from reviewed inputs')
        return ancestors

    def _root_bundle(self):
        _need_dir(self.root)
        return {name: _file(self.root / name) for name in sorted(BUNDLE_FILES)}

    def _load_state(self, anc, now):
        root = self.root
        need(root.is_dir() and not root.is_symlink(), 'census directory refused')
        need({p.name for p in root.iterdir()} == TOP_LEVEL, 'census top-level entries differ')
        _need_dir(root / 'raw')
        _need_dir(root / 'failures')
        need(_file(root / 'execution-policy.json') == self.policy_raw, 'saved policy differs')
        state = _load(_file(root / 'state.json'))
        need(type(state) is dict and set(state) == STATE_FIELDS, 'census state fields differ')
        need(type(state['schema']) is int and state['schema'] == 1 and
             state['policy_sha256'] == self.policy_pin and state['execution_commit'] == self.code_commit,
             'census identity differs')
        enabled, last = stamp(state['enabled_at']), stamp(state['last_seen'])
        need(enabled <= last <= now, 'census clock rollback')
        allowed, requests, events = list(anc.allowed), anc.requests, state['events']
        need(isinstance(events, list) and len(events) <= len(allowed), 'reservation count differs')
        previous = enabled
        for index, event in enumerate(events):
            need(type(event) is dict and set(event) == EVENT_FIELDS, 'reservation fields differ')
            need(event['kind'] == 'attempt' and type(event['weight']) is int and event['weight'] == UNITS and
                 event['day'] == allowed[index], 'reservation day or weight differs')
            need(event['request_sha256'] == _request_sha(requests[event['day']]), 'reserved request differs')
            need(previous <= stamp(event['reserved_at']) <= stamp(event['at']) <= last,
                 'reservation chronology differs')
            previous = stamp(event['at'])
        terminal = state['terminal']
        need(type(terminal) is dict, 'terminal journal differs')
        need(set(terminal) == set(allowed[:len(terminal)]), 'terminal dates are not a calendar prefix')
        pending = len(events) - len(terminal)
        need(pending in (0, 1), 'more than one unfinished reservation')
        inflight = state['inflight']
        if pending:
            need(type(inflight) is dict and inflight.get('day') == allowed[len(events) - 1] and
                 set(inflight) <= INFLIGHT_FIELDS and 'request_sha256' in inflight, 'in-flight record differs')
        else:
            need(inflight is None, 'in-flight record without reservation')
        paused = state['paused']
        need(paused is None or (isinstance(paused, str) and paused != ''), 'invalid pause')
        raw_names, failure_names = set(), set()
        for index, day in enumerate(allowed[:len(terminal)]):
            receipt, event = terminal[day], events[index]
            need(type(receipt) is dict and receipt.get('at') == event['at'], 'terminal time differs')
            if receipt.get('status') == 'fetched_ok':
                need(set(receipt) == FETCHED_FIELDS and receipt['policy_sha256'] == self.policy_pin,
                     'fetched receipt differs')
                name = 'ifs-' + day + '.json'
                raw = _file(root / 'raw' / name, MAX_RAW)
                need(digest(raw) == receipt['sha256'] and len(raw) == receipt['bytes'], 'raw receipt differs')
                raw_names.add(name)
            elif receipt.get('status') == 'recorded_run_unavailable':
                need(set(receipt) == UNAVAILABLE_FIELDS and receipt['http_status'] == 400 and
                     receipt['policy_sha256'] == self.policy_pin, 'unavailable receipt differs')
                body = _file(root / 'failures' / (day + '.unavailable.bin'), MAX_ERROR_BODY)
                need(digest(body) == receipt['sha256'] and len(body) == receipt['bytes'],
                     'unavailable body differs')
                record = _load(_file(root / 'failures' / (day + '.unavailable.json')))
                need(type(record) is dict and record.get('day') == day and
                     record.get('body_sha256') == receipt['sha256'], 'unavailable diagnostic differs')
                failure_names |= {day + '.unavailable.bin', day + '.unavailable.json'}
            else:
                raise CacheRefusal('unknown terminal status')
        optional_raw, optional_failure = set(), set()
        if pending:
            day = allowed[len(events) - 1]
            if 'response_sha256' in inflight and os.path.lexists(root / 'raw' / ('ifs-' + day + '.json')):
                raw = _file(root / 'raw' / ('ifs-' + day + '.json'), MAX_RAW)
                need(digest(raw) == inflight.get('response_sha256') and len(raw) == inflight.get('response_bytes'),
                     'in-flight response differs')
                optional_raw.add('ifs-' + day + '.json')
            if 'body_sha256' in inflight:
                path = root / 'failures' / (day + '.unavailable.bin')
                if os.path.lexists(path):
                    body = _file(path, MAX_ERROR_BODY)
                    need(digest(body) == inflight['body_sha256'] and len(body) == inflight.get('body_bytes'),
                         'in-flight body differs')
                    optional_failure.add(path.name)
                optional_failure.add(day + '.unavailable.json')
        if events and paused:
            optional_failure.add(events[-1]['day'] + '.stop.json')
        raw_present = {p.name for p in (root / 'raw').iterdir()}
        failure_present = {p.name for p in (root / 'failures').iterdir()}
        need(raw_names <= raw_present <= raw_names | optional_raw, 'raw entries differ')
        need(failure_names <= failure_present <= failure_names | optional_failure, 'failure entries differ')
        return state

    def _stop(self, state, day, reason, *, attempted):
        """Durable pause first; the stop receipt is diagnostic and cannot undo the pause."""
        state['paused'] = reason
        save_state(self.root, state)
        try:
            write_new(self.root / 'failures' / (day + '.stop.json'),
                      canonical({'day': day, 'reason': reason, 'policy_sha256': self.policy_pin}))
        except Exception:
            pass
        return {'status': 'paused', 'reason': reason, 'day': day,
                'network_requests': 1 if attempted else 0, 'automatic_retries': 0}

    def _response(self, state, day, request, raw, now, clock):
        if type(raw) is not bytes or len(raw) > MAX_RAW:
            return self._stop(state, day, 'response_byte_limit', attempted=True)
        finished = _finished(clock, now)
        if finished is None:
            return self._stop(state, day, 'clock_failed_after_response', attempted=True)
        _close(state, finished)
        state['inflight']['response_sha256'] = digest(raw)
        state['inflight']['response_bytes'] = len(raw)
        save_state(self.root, state)
        target = self.root / 'raw' / ('ifs-' + day + '.json')
        try:
            write_new(target, raw)
            back = _file(target, MAX_RAW)
            need(digest(back) == digest(raw) and len(back) == len(raw), 'raw reread differs')
            CacheWorker(self.root).validate(raw, request)
        except Exception as exc:
            return self._stop(state, day, 'response_not_accepted:' + type(exc).__name__, attempted=True)
        state['terminal'][day] = {'status': 'fetched_ok', 'sha256': digest(raw), 'bytes': len(raw),
                                  'at': finished.isoformat(), 'policy_sha256': self.policy_pin}
        state['inflight'] = None
        save_state(self.root, state)
        return {'status': 'fetched_ok', 'day': day, 'completed_census': len(state['terminal']),
                'network_requests': 1, 'automatic_retries': 0}

    def _transport_failure(self, state, day, request, exc, now, clock):
        body = classify_unavailable(exc, day=day, request=request)
        if body is None:
            return self._stop(state, day, _failure_reason(exc), attempted=True)
        finished = _finished(clock, now)
        if finished is None:
            return self._stop(state, day, 'clock_failed_after_unavailable', attempted=True)
        _close(state, finished)
        state['inflight']['body_sha256'] = digest(body)
        state['inflight']['body_bytes'] = len(body)
        save_state(self.root, state)
        receipt = canonical({'day': day, 'http_status': 400, 'error_class': 'HTTPError',
                             'returncode': exc.child_returncode, 'body_bytes': len(body),
                             'body_sha256': digest(body), 'request_sha256': _request_sha(request),
                             'model': _model_hour(request)[0], 'hour': _model_hour(request)[1],
                             'policy_sha256': self.policy_pin})
        failures = self.root / 'failures'
        try:
            write_new(failures / (day + '.unavailable.bin'), body)
            write_new(failures / (day + '.unavailable.json'), receipt)
            need(digest(_file(failures / (day + '.unavailable.bin'), MAX_ERROR_BODY)) == digest(body),
                 'unavailable body reread differs')
            need(digest(_file(failures / (day + '.unavailable.json'))) == digest(receipt),
                 'unavailable receipt reread differs')
        except Exception as write_exc:
            return self._stop(state, day, 'unavailable_receipt_not_durable:' + type(write_exc).__name__,
                              attempted=True)
        state['terminal'][day] = {'status': 'recorded_run_unavailable', 'sha256': digest(body),
                                  'bytes': len(body), 'at': finished.isoformat(),
                                  'policy_sha256': self.policy_pin, 'http_status': 400}
        state['inflight'] = None
        save_state(self.root, state)
        return {'status': 'recorded_run_unavailable', 'day': day, 'network_requests': 1,
                'automatic_retries': 0}

    def step(self, *, fetch, clock, guard):
        with self.locks():
            now = utc(clock())
            anc = self._preflight(self._root_bundle(), now)
            state = self._load_state(anc, now)
            if state['paused']:
                return {'status': 'paused', 'reason': state['paused'], 'network_requests': 0}
            if state['inflight']:
                return self._stop(state, state['inflight']['day'], 'uncertain_reservation_requires_review',
                                  attempted=False)
            allowed = anc.allowed
            n = len(state['events'])
            if n == len(allowed):
                return {'status': COMPLETE, 'network_requests': 0, 'automatic_retries': 0}
            fresh = utc(clock())
            need(fresh >= now, 'clock rollback during preflight')
            now = fresh
            events = [*anc.parent_events, *anc.gap_events, *state['events']]
            combined_attempts = sum(e['kind'] == 'attempt' for e in events)
            combined_units = sum(e['weight'] for e in events)
            need(combined_attempts + 1 <= COMBINED_LIMIT['attempts'] and
                 combined_units + UNITS <= COMBINED_LIMIT['units'], 'combined budget exhausted')
            guard()
            allowed_at = CacheWorker(self.root).wait_until({'events': events}, now)
            if allowed_at > now:
                return {'status': 'quota_wait', 'not_before': allowed_at.isoformat(),
                        'network_requests': 0, 'automatic_retries': 0}
            day = allowed[n]
            request = anc.requests[day]
            url = request['url']
            _check_endpoint(url)
            request_sha = _request_sha(request)
            state['events'].append({'kind': 'attempt', 'day': day, 'weight': UNITS,
                                    'reserved_at': now.isoformat(), 'at': now.isoformat(),
                                    'request_sha256': request_sha})
            state['inflight'] = {'day': day, 'request_sha256': request_sha}
            state['last_seen'] = now.isoformat()
            save_state(self.root, state)  # Durable before any transport; a write error means zero GET.
            try:
                guard()
            except Exception as exc:
                return self._stop(state, day, 'guard_failed_after_reservation:' + type(exc).__name__,
                                  attempted=False)
            try:
                raw = fetch(url, GET_TIMEOUT, MAX_RAW)
            except TransportFailure as exc:
                return self._transport_failure(state, day, request, exc, now, clock)
            except Exception as exc:
                return self._stop(state, day, 'transport_failed:' + type(exc).__name__, attempted=True)
            return self._response(state, day, request, raw, now, clock)

    def initialize(self, bundle, *, now, guard):
        with self.locks():
            self._preflight(bundle, now)
            need(not self.root.exists() and not self.root.is_symlink() and
                 self.root.parent.is_dir() and not self.root.parent.is_symlink(), 'new namespace required')
            guard()
            self.root.mkdir(mode=0o700)
            sync_dir(self.root.parent)
            (self.root / 'raw').mkdir(mode=0o700)
            (self.root / 'failures').mkdir(mode=0o700)
            sync_dir(self.root)
            for name in sorted(BUNDLE_FILES):
                write_new(self.root / name, bundle[name])
            write_new(self.root / 'execution-policy.json', self.policy_raw)
            state = {'schema': 1, 'policy_sha256': self.policy_pin, 'execution_commit': self.code_commit,
                     'enabled_at': now.isoformat(), 'last_seen': now.isoformat(), 'events': [],
                     'terminal': {}, 'inflight': None, 'paused': None}
            save_state(self.root, state)
            return {'status': 'census_initialized', 'network_requests': 0, 'automatic_retries': 0,
                    'calendar_dates': len(self.p['allowed_dates']), 'baseline': BASELINE}

    def preflight_offline(self, bundle, *, now):
        with self.locks():
            anc = self._preflight(bundle, now)
            return {'status': 'offline_preflight_passed', 'network_requests': 0, 'automatic_retries': 0,
                    'calendar_dates': len(anc.allowed), 'baseline': BASELINE,
                    'census_limit': CENSUS_LIMIT, 'combined_limit': COMBINED_LIMIT,
                    'parent_events': len(anc.parent_events), 'gap_events': len(anc.gap_events)}

    def publication(self, *, now, actuals=None):
        """Read-only publication: calendar provenance, valid-run count, pair counts or not_computed."""
        with self.locks():
            anc = self._preflight(self._root_bundle(), now)
            state = self._load_state(anc, now)
        calendar = _calendar(anc, state)
        valid_days, valid, invalid = set(), 0, 0
        for row in calendar:
            if row['status'] != 'fetched_ok':
                continue
            directory = self.parent / 'raw' if row['source'] == 'parent' else self.root / 'raw'
            raw = _file(directory / ('ifs-' + row['issue_date'] + '.json'), MAX_RAW)
            try:
                CacheWorker(self.root).validate(raw, _request(date.fromisoformat(row['issue_date'])))
                valid += 1
                valid_days.add(date.fromisoformat(row['issue_date']))
            except Exception:
                invalid += 1
        counts = {}
        for row in calendar:
            counts[row['status']] = counts.get(row['status'], 0) + 1
        terminal = len(state['terminal'])
        return {
            'schema': 1, 'id': CENSUS_ID,
            'status': COMPLETE if terminal == len(anc.allowed) else 'census_incomplete',
            'calendar': calendar, 'status_counts': counts,
            'valid_runs': valid, 'invalid_runs': invalid,
            'pair_counts': count_pairs(sorted(valid_days), actuals),
            'declarations': {'census_v1_declared': False, 'strict607_declared': False,
                             'live_m3_declared': False},
        }


def _calendar(anc, state):
    rows = []
    gap_open = {e['day'] for e in anc.gap_events}
    start, end = date.fromisoformat(FIRST_ISSUE), date.fromisoformat(LAST_ISSUE)
    issue = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    need(len(issue) == ISSUE_DATES, 'issue calendar differs')
    for day in issue:
        if day in anc.parent_completed:
            status, source = 'fetched_ok', 'parent'
        elif day == RECORDED_GAP:
            status, source = 'recorded_run_unavailable', 'parent'
        elif day in gap_open:
            status, source = 'reserved_or_stop', 'gap'
        elif day in state['terminal']:
            status, source = state['terminal'][day]['status'], 'census'
        elif state['inflight'] and state['inflight']['day'] == day:
            status, source = 'reserved_or_stop', 'census'
        else:
            status, source = 'unrequested', None
        rows.append({'issue_date': day, 'status': status, 'source': source})
    return rows


def count_pairs(valid_days, actuals):
    """Mature event-time pairs per origin, point and lead. Zero and incomplete cells are kept.

    actuals maps (point_code, iso_day) to observed hour count. Without a pinned actual
    archive no count is produced.
    """
    if actuals is None:
        return {'status': 'not_computed', 'reason': NOT_COMPUTED}
    cells = []
    for origin in origins():
        for point in OPENMETEO_POINTS_V1:
            for lead in range(1, HORIZON + 1):
                runs = [d for d in valid_days if eligible(d, origin, lead)]
                pairs = sum(1 for d in runs if actuals.get(
                    (point.code, (d + timedelta(days=lead)).isoformat()), 0) >= OBSERVATION_MIN_HOURS)
                cells.append({'origin': origin.isoformat(), 'point': point.code, 'lead': lead,
                              'eligible_valid_runs': len(runs), 'pairs': pairs,
                              'incomplete': len(runs) - pairs})
    return {'status': 'computed', 'cells': cells}


def _validate_policy(p, *, parent, gap, root, code_commit):
    need(type(p) is dict and set(p) == POLICY_FIELDS, 'execution policy fields differ')
    need(type(p['schema']) is int and p['schema'] == 1 and p['id'] == CENSUS_ID,
         'execution policy identity differs')
    need(isinstance(code_commit, str) and COMMIT.match(code_commit) is not None and
         p['execution_commit'] == code_commit, 'execution commit differs')
    p_root, g_root, c_root = parent.resolve(), gap.resolve(), root.resolve()
    need(p['root'] == str(c_root), 'census root differs')
    need(_same_pair(p_root, g_root) and _same_pair(p_root, c_root) and _same_pair(g_root, c_root),
         'separate canonical namespaces required')
    pins = p['ancestor_pins']
    need(type(pins) is dict and set(pins) == {'parent', 'gap', 'unavailable_evidence',
                                              'inherited_manifest_sha256'}, 'ancestor pin fields differ')
    pp, gp, ev = pins['parent'], pins['gap'], pins['unavailable_evidence']
    need(type(pp) is dict and set(pp) == {'root', 'state_sha256', 'plan_sha256', 'policy_sha256'} and
         pp['root'] == str(p_root) and set(pp['policy_sha256']) == PARENTS, 'parent pin fields differ')
    need(type(gp) is dict and set(gp) == {'root', 'state_sha256', 'contract_sha256',
                                          'execution_policy_sha256', 'metadata_sha256'} and
         gp['root'] == str(g_root) and set(gp['metadata_sha256']) == {'science.json', 'inventory.json',
                                                                       'authorization.txt'},
         'gap pin fields differ')
    need(type(ev) is dict and set(ev) == {'parent', 'gap'}, 'evidence pin fields differ')
    for value in [pp['state_sha256'], pp['plan_sha256'], *pp['policy_sha256'].values(),
                  gp['state_sha256'], gp['contract_sha256'], gp['execution_policy_sha256'],
                  *gp['metadata_sha256'].values(), ev['parent'], ev['gap'],
                  pins['inherited_manifest_sha256'], p['authorization_sha256']]:
        _sha(value)
    need(type(p['bundle_pins']) is dict and set(p['bundle_pins']) == PINNED_BUNDLE, 'bundle pin fields differ')
    for value in p['bundle_pins'].values():
        _sha(value)
    need(_same(p['excluded_dates'], EXCLUDED), 'excluded dates differ')
    allowed = _dates(p['allowed_dates'])
    need(len(allowed) == 111 and len(set(allowed)) == 111 and allowed == sorted(allowed) and
         not set(allowed) & set(EXCLUDED), 'policy calendar differs')
    need(_same(p['budget'], BUDGET), 'budget differs from reviewed numbers')
    need(_same(p['terminal_error_rule'], TERMINAL_ERROR_RULE), 'terminal error rule differs')
    need(_same(p['transport'], TRANSPORT), 'transport differs')
    need(_same(p['publication_scope'], PUBLICATION_SCOPE), 'publication scope differs')
    return p
