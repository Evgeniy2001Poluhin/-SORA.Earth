"""Synthetic ancestor roots P, G and census C in temporary directories; no network.

Every transport is a scripted fake that fails on any call it was not given, and a
socket block is installed for each test as a backstop. Schemas follow the real
ledgers; the weather bytes are synthetic.
"""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import base64
import importlib.util
import fcntl
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

from app.services.environmental.regions import OPENMETEO_POINTS_V1
from app.services.forecasting import replay_source_census as m
from app.services.forecasting.replay_cache_worker import (CacheRefusal, CacheWorker, MAX_RAW,
                                                          TransportFailure, digest, need, save_state)
from app.services.forecasting.replay_gap_collector import PARENTS
from app.services.forecasting.replay_source_gap_contract import canonical

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'forecast_replay_source_census.py'
NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
OLD = NOW - timedelta(days=3)
CODE = '5' * 40
AUTH = b'synthetic owner authorization text'
GOOD = b'good'
PROBE_DAY = '2025-08-05'
HIST = '2025-08-06'
_REAL = {'save_state': m.save_state, 'write_new': m.write_new}
ORIGINAL_VALIDATE = CacheWorker.validate


class UnexpectedGet(BaseException):
    """BaseException, so no handler in the worker can swallow an unscripted request."""


def forbidden(*args):
    raise UnexpectedGet(args)


class Fake:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, url, timeout, limit):
        self.calls.append((url, timeout, limit))
        if not self.outcomes:
            raise UnexpectedGet('no scripted outcome for this request')
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class Clock:
    """Returns the scripted instants in order; running out is a clock failure."""

    def __init__(self, *times):
        self.times = list(times)

    def __call__(self):
        if not self.times:
            raise RuntimeError('clock failed')
        return self.times.pop(0)


def clock_at(moment):
    return Clock(moment, moment, moment, moment)


def reason(day, model='ecmwf_ifs', hour='00'):
    return 'The requested model run is not available. Model: %s, run: %sT%s:00Z' % (model, day, hour)


def body(day, *, model='ecmwf_ifs', run_day=None, hour='00', text=None, error=True):
    text = text if text is not None else reason(run_day or day, model, hour)
    return json.dumps({'reason': text, 'error': error}, separators=(',', ':')).encode()


def unavailable(day, *, raw=None, code=400, cls='HTTPError', truncated=False, rc=2):
    raw = raw if raw is not None else body(day)
    meta = {'base64': base64.b64encode(raw).decode(), 'bytes': len(raw), 'sha256': digest(raw),
            'truncated': truncated}
    return TransportFailure(code, error_class=cls, returncode=rc, http_error_body=meta)


def iso(moment):
    return moment.isoformat()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise UnexpectedGet('network blocked in census tests')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
    monkeypatch.setattr(urllib.request.OpenerDirector, 'open', blocked)


class World:
    def __init__(self, tmp, monkeypatch):
        self.tmp = Path(tmp)
        self.parent, self.gap, self.root = self.tmp / 'cache', self.tmp / 'run', self.tmp / 'census'
        self.plan = m.reviewed_plan()
        self.requests = {r['issue_date']: r for r in self.plan['requests']}
        reuse = {r['issue_date'] for r in self.plan['reuse_candidates']}
        calendar = [(date(2024, 4, 1) + timedelta(days=i)).isoformat() for i in range(607)]
        after = [d for d in calendar if d >= HIST and d not in reuse]
        self.gap_allowed = after[:112]
        self.allowed = self.gap_allowed[1:]
        self.completed = [d for d in calendar if d != PROBE_DAY and d not in set(self.gap_allowed)]
        assert len(self.completed) == 494 and self.gap_allowed[0] == HIST
        assert set(self.gap_allowed) <= self.requests.keys()
        self.p_events = ([{'kind': 'prior_allowance', 'weight': 2472, 'at': iso(OLD)}] +
                         [{'kind': 'attempt', 'day': d, 'weight': 21, 'reserved_at': iso(OLD), 'at': iso(OLD)}
                          for d in self.completed[:484] + [PROBE_DAY]])
        self.g_last = NOW - timedelta(hours=2)
        self.g_events = [{'kind': 'attempt', 'day': HIST, 'weight': 21,
                          'reserved_at': iso(self.g_last), 'at': iso(self.g_last)}]
        self._write_ancestor_files()
        monkeypatch.setattr(CacheWorker, 'validate',
                            lambda self_, raw, request: need(raw == GOOD, 'synthetic grid failure'))
        self.pin()

    def _write_ancestor_files(self):
        for path in (self.parent / 'raw', self.gap / 'raw', self.gap / 'failures'):
            path.mkdir(parents=True)
        for name in ('controller.lock', 'worker.lock'):
            (self.parent / name).write_bytes(b'')
        (self.parent / 'plan.json').write_bytes(canonical(self.plan))
        for day in self.completed:
            (self.parent / 'raw' / ('ifs-' + day + '.json')).write_bytes(GOOD)
        for name in PARENTS:
            (self.parent / name).write_bytes(canonical({'id': name}))
        (self.parent / 'http-probe-response.bin').write_bytes(body(PROBE_DAY))
        (self.gap / 'contract.json').write_bytes(canonical(
            {'schema': 1, 'id': 'synthetic-gap-contract', 'acquisition': {'allowed_dates': self.gap_allowed}}))
        (self.gap / 'science.json').write_bytes(b'{"synthetic": "science"}')
        (self.gap / 'inventory.json').write_bytes(b'{"synthetic": "inventory"}')
        (self.gap / 'authorization.txt').write_bytes(b'synthetic gap authorization')
        (self.gap / 'execution-policy.json').write_bytes(canonical({'schema': 1, 'id': 'M3-P1-COMPANION112'}))
        (self.gap / 'failures' / (HIST + '.http-error.json')).write_bytes(canonical(
            {'error_class': 'HTTPError', 'http_status': 400, 'returncode': 2,
             'prefix': {'bytes': len(body(HIST)), 'sha256': digest(body(HIST)), 'truncated': False}}))
        (self.gap / 'failures' / (HIST + '.http-error-prefix.bin')).write_bytes(body(HIST))

    def pin(self):
        """Rewrite both ledgers' state files and derive every pin, bundle and policy from disk."""
        plan_raw = (self.parent / 'plan.json').read_bytes()
        pstate = {'schema': 1, 'plan_sha256': digest(plan_raw), 'code_commit': 'a' * 40,
                  'last_seen': iso(OLD),
                  'completed': {d: {'sha256': digest(GOOD), 'bytes': len(GOOD), 'source': {'kind': 'synthetic'},
                                    'ledger_at': iso(OLD), 'historical_publication': 'not attested'}
                                for d in self.completed},
                  'events': self.p_events, 'inflight': None,
                  'paused': 'request_failed:TransportFailure:400', 'http_probe': {'outcome': 'recorded'}}
        (self.parent / 'state.json').write_bytes(canonical(pstate))
        gstate = {'schema': 1, 'policy_sha256': '2' * 64, 'execution_commit': 'b' * 40,
                  'parent_state_sha256': '1' * 64, 'enabled_at': iso(self.g_last),
                  'last_seen': iso(self.g_last), 'events': self.g_events, 'completed': {},
                  'inflight': {'day': HIST}, 'paused': 'request_failed:TransportFailure:400'}
        (self.gap / 'state.json').write_bytes(canonical(gstate))
        manifest_raw = canonical(m.inherited_manifest(self.parent, self.gap, self.completed))
        gap_contract = (self.gap / 'contract.json').read_bytes()
        contract_raw = canonical(m.census_contract(gap_contract, manifest_sha256=digest(manifest_raw)))
        self.bundle = {'contract.json': contract_raw, 'inherited-manifest.json': manifest_raw,
                       'authorization.txt': AUTH}
        self.set_policy(self.policy_for())

    def policy_for(self):
        return {
            'schema': 1, 'id': m.CENSUS_ID, 'execution_commit': CODE, 'root': str(self.root.resolve()),
            'ancestor_pins': {
                'parent': {'root': str(self.parent.resolve()),
                           'state_sha256': digest((self.parent / 'state.json').read_bytes()),
                           'plan_sha256': digest((self.parent / 'plan.json').read_bytes()),
                           'policy_sha256': {n: digest((self.parent / n).read_bytes()) for n in PARENTS}},
                'gap': {'root': str(self.gap.resolve()),
                        'state_sha256': digest((self.gap / 'state.json').read_bytes()),
                        'contract_sha256': digest((self.gap / 'contract.json').read_bytes()),
                        'execution_policy_sha256': digest((self.gap / 'execution-policy.json').read_bytes()),
                        'metadata_sha256': {n: digest((self.gap / n).read_bytes()) for n in
                                            ('science.json', 'inventory.json', 'authorization.txt')}},
                'unavailable_evidence': {
                    'parent': digest((self.parent / 'http-probe-response.bin').read_bytes()),
                    'gap': digest((self.gap / 'failures' / (HIST + '.http-error-prefix.bin')).read_bytes())},
                'inherited_manifest_sha256': digest(self.bundle['inherited-manifest.json']),
            },
            'bundle_pins': {'contract.json': digest(self.bundle['contract.json']),
                            'inherited-manifest.json': digest(self.bundle['inherited-manifest.json'])},
            'allowed_dates': self.allowed, 'excluded_dates': list(m.EXCLUDED), 'budget': m.BUDGET,
            'terminal_error_rule': m.TERMINAL_ERROR_RULE, 'transport': m.TRANSPORT,
            'publication_scope': m.PUBLICATION_SCOPE, 'authorization_sha256': digest(AUTH),
        }

    def set_policy(self, policy):
        self.policy_raw = canonical(policy)
        self.policy_pin = digest(self.policy_raw)
        self.c = m.CensusCollector(self.parent, self.gap, self.root, self.policy_raw, self.policy_pin,
                                   code_commit=CODE)

    def init(self):
        return self.c.initialize(self.bundle, now=NOW, guard=lambda: None)

    def state(self):
        return json.loads((self.root / 'state.json').read_bytes())

    def save(self, state):
        save_state(self.root, state)

    def ancestors(self):
        out = {}
        for base in (self.parent, self.gap):
            for path in sorted(base.rglob('*')):
                if path.is_file():
                    out[str(path)] = path.read_bytes()
        return out

    def url(self, day):
        return self.requests[day]['url']


@pytest.fixture
def w(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def step(collector, fetch, t=NOW, guard=None, clock=None):
    return collector.step(fetch=fetch, clock=clock or clock_at(t), guard=guard or (lambda: None))


def arm(monkeypatch, name, nth):
    """Crash (SystemExit) on the nth call of a durable writer inside the census module."""
    count = {'n': 0}
    original = _REAL[name]

    def crashing(*args, **kwargs):
        count['n'] += 1
        if count['n'] == nth:
            raise SystemExit('synthetic crash at a durable boundary')
        return original(*args, **kwargs)
    monkeypatch.setattr(m, name, crashing)


# ---------------------------------------------------------------- calendar and constants

def test_calendar_is_exact_111_in_order_and_excludes_both_gap_dates(w):
    assert len(w.allowed) == 111 and len(set(w.allowed)) == 111
    assert w.allowed == sorted(w.allowed)
    assert HIST not in w.allowed and PROBE_DAY not in w.allowed
    assert w.allowed == [d for d in w.gap_allowed if d != HIST]
    assert m.source_calendar((w.gap / 'contract.json').read_bytes()) == w.allowed
    assert w.c.p['allowed_dates'] == w.allowed


def test_budget_numbers_are_the_reviewed_contract_numbers():
    assert m.BASELINE == {'attempts': 486, 'units': 12678}
    assert m.CENSUS_LIMIT == {'attempts': 111, 'units': 2331}
    assert m.COMBINED_LIMIT == {'attempts': 597, 'units': 15009}
    assert m.BASELINE['attempts'] + m.CENSUS_LIMIT['attempts'] == m.COMBINED_LIMIT['attempts']
    assert m.BASELINE['units'] + m.CENSUS_LIMIT['units'] == m.COMBINED_LIMIT['units']
    assert m.CENSUS_LIMIT['units'] == 111 * m.UNITS


# ---------------------------------------------------------------- zero-request paths

def test_offline_preflight_reports_baseline_and_makes_no_request(w):
    before = w.ancestors()
    result = w.c.preflight_offline(w.bundle, now=NOW)
    assert result['status'] == 'offline_preflight_passed' and result['network_requests'] == 0
    assert result['baseline'] == {'attempts': 486, 'units': 12678}
    assert result['calendar_dates'] == 111 and result['parent_events'] == 486 and result['gap_events'] == 1
    assert not w.root.exists()
    assert w.ancestors() == before


def test_initialize_creates_only_the_new_namespace_and_no_request(w):
    before = w.ancestors()
    result = w.init()
    assert result['status'] == 'census_initialized' and result['network_requests'] == 0
    assert {p.name for p in w.root.iterdir()} == m.TOP_LEVEL
    assert list((w.root / 'raw').iterdir()) == [] and list((w.root / 'failures').iterdir()) == []
    assert w.ancestors() == before
    with pytest.raises(CacheRefusal):
        w.init()


# ---------------------------------------------------------------- single reserved request

def test_one_step_one_request_and_the_reservation_is_on_disk_inside_it(w):
    w.init()
    seen = []

    def fetch(url, timeout, limit):
        state = json.loads((w.root / 'state.json').read_bytes())
        seen.append((len(state['events']), state['inflight'], state['events'][-1]['day']))
        assert (timeout, limit) == (m.GET_TIMEOUT, MAX_RAW)
        return GOOD
    result = w.c.step(fetch=fetch, clock=clock_at(NOW), guard=lambda: None)
    assert result['status'] == 'fetched_ok' and result['network_requests'] == 1
    assert len(seen) == 1
    count, inflight, day = seen[0]
    assert count == 1 and inflight['day'] == w.allowed[0] and day == w.allowed[0]


def test_fetched_day_gets_receipt_and_next_request_waits_four_seconds_after_response(w):
    w.init()
    fake = Fake(GOOD, GOOD)
    assert step(w.c, fake, NOW)['status'] == 'fetched_ok'
    early = step(w.c, forbidden, NOW + timedelta(seconds=3))
    assert early['status'] == 'quota_wait'
    assert datetime.fromisoformat(early['not_before']) == NOW + timedelta(seconds=4)
    assert step(w.c, fake, NOW + timedelta(seconds=4))['status'] == 'fetched_ok'
    assert [c[0] for c in fake.calls] == [w.url(w.allowed[0]), w.url(w.allowed[1])]


def test_quota_wait_returns_not_before_and_never_sleeps(w, monkeypatch):
    w.init()

    def no_sleep(*args):
        raise AssertionError('worker must not sleep')
    monkeypatch.setattr(time, 'sleep', no_sleep)
    assert step(w.c, Fake(GOOD), NOW)['status'] == 'fetched_ok'
    result = step(w.c, forbidden, NOW + timedelta(seconds=1))
    assert result['status'] == 'quota_wait' and result['network_requests'] == 0


def test_unavailable_then_success_then_unavailable_continues_the_calendar(w):
    w.init()
    fake = Fake(unavailable(w.allowed[0]), GOOD, unavailable(w.allowed[2]))
    assert step(w.c, fake, NOW)['status'] == 'recorded_run_unavailable'
    assert step(w.c, fake, NOW + timedelta(seconds=4))['status'] == 'fetched_ok'
    assert step(w.c, fake, NOW + timedelta(seconds=8))['status'] == 'recorded_run_unavailable'
    state = w.state()
    assert [state['terminal'][d]['status'] for d in w.allowed[:3]] == [
        'recorded_run_unavailable', 'fetched_ok', 'recorded_run_unavailable']
    assert state['inflight'] is None and state['paused'] is None


def test_recorded_unavailable_body_and_receipt_are_on_disk_before_terminal(w):
    w.init()
    day = w.allowed[0]
    assert step(w.c, Fake(unavailable(day)))['status'] == 'recorded_run_unavailable'
    failures = w.root / 'failures'
    assert (failures / (day + '.unavailable.bin')).read_bytes() == body(day)
    receipt = json.loads((failures / (day + '.unavailable.json')).read_bytes())
    assert receipt['body_sha256'] == digest(body(day)) and receipt['day'] == day
    assert receipt['http_status'] == 400 and receipt['model'] == 'ecmwf_ifs' and receipt['hour'] == '00'


def test_four_seconds_after_recorded_unavailable_counts_from_its_finish(w):
    w.init()
    clock = Clock(NOW, NOW, NOW + timedelta(seconds=2))
    result = w.c.step(fetch=Fake(unavailable(w.allowed[0])), clock=clock, guard=lambda: None)
    assert result['status'] == 'recorded_run_unavailable'
    early = step(w.c, forbidden, NOW + timedelta(seconds=3))
    assert early['status'] == 'quota_wait'
    assert datetime.fromisoformat(early['not_before']) == NOW + timedelta(seconds=6)


# ---------------------------------------------------------------- classifier

REFUSED = {
    'wrong_model': lambda d: unavailable(d, raw=body(d, model='ecmwf_gfs')),
    'wrong_date': lambda d: unavailable(d, raw=body(d, run_day='2099-01-01')),
    'wrong_hour': lambda d: unavailable(d, raw=body(d, hour='06')),
    'old_substring_text': lambda d: unavailable(d, raw=json.dumps(
        {'reason': 'requested model run unavailable', 'error': True}).encode()),
    'text_with_prefix': lambda d: unavailable(d, raw=json.dumps(
        {'reason': 'Upstream: ' + reason(d), 'error': True}).encode()),
    'error_false': lambda d: unavailable(d, raw=body(d, error=False)),
    'error_string': lambda d: unavailable(d, raw=json.dumps({'reason': reason(d), 'error': 'true'}).encode()),
    'malformed_json': lambda d: unavailable(d, raw=b'{"reason":'),
    'duplicate_key': lambda d: unavailable(d, raw=b'{"reason":"a","reason":"%s","error":true}' % reason(d).encode()),
    'extra_key': lambda d: unavailable(d, raw=json.dumps(
        {'reason': reason(d), 'error': True, 'code': 1}).encode()),
    'truncated_flag_with_complete_body': lambda d: unavailable(
        d, raw=body(d) + b' ' * (1024 - len(body(d))), truncated=True),
    'http_500_with_the_body': lambda d: unavailable(d, code=500),
    'http_404_with_the_body': lambda d: unavailable(d, code=404),
    'http_error_class_is_url': lambda d: unavailable(d, cls='URLError'),
    'no_body': lambda d: TransportFailure(400, error_class='HTTPError', returncode=2),
    'timeout': lambda d: TimeoutError('deadline'),
    'generic_error': lambda d: RuntimeError('boom'),
}


@pytest.mark.parametrize('case', sorted(REFUSED))
def test_classifier_accepts_only_the_exact_recorded_answer(w, case):
    w.init()
    fake = Fake(REFUSED[case](w.allowed[0]))
    assert step(w.c, fake)['status'] == 'paused'
    assert w.state()['terminal'] == {}
    assert step(w.c, forbidden, NOW + timedelta(minutes=10))['status'] == 'paused'
    assert len(fake.calls) == 1


# ---------------------------------------------------------------- global STOP classes

def _oversized():
    return b'x' * (MAX_RAW + 1)


STOPS = {
    'timeout': lambda d: TimeoutError('deadline'),
    'http_500_with_recorded_body': lambda d: unavailable(d, code=500),
    'url_error': lambda d: TransportFailure(None, error_class='URLError', returncode=2),
    'response_not_bytes': lambda d: 'not bytes',
    'response_over_byte_limit': lambda d: _oversized(),
    'weather_grid_rejected': lambda d: b'bad',
}


@pytest.mark.parametrize('name', sorted(STOPS))
def test_every_global_stop_pauses_and_no_later_request_is_made(w, name):
    w.init()
    fake = Fake(STOPS[name](w.allowed[0]))
    result = step(w.c, fake)
    assert result['status'] == 'paused' and result['day'] == w.allowed[0]
    assert len(fake.calls) == 1
    assert step(w.c, forbidden, NOW + timedelta(hours=1))['status'] == 'paused'
    assert w.state()['terminal'] == {}


def test_clock_failure_after_response_is_a_stop_and_keeps_the_charge(w):
    w.init()
    result = w.c.step(fetch=Fake(GOOD), clock=Clock(NOW, NOW), guard=lambda: None)
    assert result['status'] == 'paused' and result['reason'] == 'clock_failed_after_response'
    assert len(w.state()['events']) == 1 and w.state()['terminal'] == {}
    assert step(w.c, forbidden, NOW + timedelta(hours=1))['status'] == 'paused'


def test_clock_running_backwards_after_response_is_a_stop(w):
    w.init()
    result = w.c.step(fetch=Fake(GOOD), clock=Clock(NOW, NOW, NOW - timedelta(seconds=1)), guard=lambda: None)
    assert result['status'] == 'paused' and result['reason'] == 'clock_failed_after_response'


def test_clock_running_backwards_before_reservation_refuses_without_state_change(w):
    w.init()
    result = w.c.step(fetch=forbidden, clock=Clock(NOW, NOW - timedelta(seconds=1)), guard=lambda: None)
    assert result['status'] == 'paused' and result['network_requests'] == 0
    assert w.state()['events'] == [] and w.state()['paused']


def test_raw_bytes_that_do_not_reread_are_a_stop(w, monkeypatch):
    w.init()

    def corrupting(path, raw):
        return _REAL['write_new'](path, b'corrupt-on-disk' if path.parent == w.root / 'raw' else raw)
    monkeypatch.setattr(m, 'write_new', corrupting)
    assert step(w.c, Fake(GOOD))['status'] == 'paused'
    # Damaged evidence on disk is refused at load, before any request is considered.
    with pytest.raises(CacheRefusal):
        step(w.c, forbidden, NOW + timedelta(hours=1))


# ---------------------------------------------------------------- guards and reservation

def test_first_guard_refuses_before_reservation_and_request(w):
    w.init()
    def guard():
        raise RuntimeError('not clean source')
    result = w.c.step(fetch=forbidden, clock=clock_at(NOW), guard=guard)
    assert result['status'] == 'paused' and result['network_requests'] == 0
    assert w.state()['events'] == [] and w.state()['paused']


def test_second_guard_after_reservation_stops_before_transport(w):
    w.init()
    calls = []

    def guard():
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError('source drift between reservation and transport')
    result = w.c.step(fetch=forbidden, clock=clock_at(NOW), guard=guard)
    assert result['status'] == 'paused' and result['network_requests'] == 0
    assert result['reason'] == 'guard_failed_after_reservation:RuntimeError'
    assert len(w.state()['events']) == 1


def test_reservation_write_failure_means_zero_requests(w, monkeypatch):
    w.init()

    def failing(root, state):
        if state.get('inflight') and state['inflight'].get('request_sha256'):
            raise OSError('synthetic disk failure on reservation')
        return _REAL['save_state'](root, state)
    monkeypatch.setattr(m, 'save_state', failing)
    with pytest.raises(OSError):
        step(w.c, forbidden)
    assert w.state()['events'] == []


# ---------------------------------------------------------------- crash boundaries

GOOD_BOUNDARIES = [('save_state', 1), ('save_state', 2), ('save_state', 3), ('write_new', 1)]
UNAVAILABLE_BOUNDARIES = [('save_state', 1), ('save_state', 2), ('save_state', 3),
                          ('write_new', 1), ('write_new', 2)]


@pytest.mark.parametrize('outcome,which,nth', [('good', *b) for b in GOOD_BOUNDARIES] +
                         [('unavailable', *b) for b in UNAVAILABLE_BOUNDARIES])
def test_crash_at_each_durable_boundary_never_repeats_the_attempt(w, monkeypatch, outcome, which, nth):
    w.init()
    first = Fake(GOOD if outcome == 'good' else unavailable(w.allowed[0]))
    arm(monkeypatch, which, nth)
    with pytest.raises(SystemExit):
        step(w.c, first)
    monkeypatch.setattr(m, which, _REAL[which])
    second = Fake(GOOD, GOOD)
    result = step(w.c, second, NOW + timedelta(minutes=5))
    assert len(first.calls) + len(second.calls) <= 1
    if first.calls:
        assert result['status'] == 'paused' and second.calls == []
    else:
        assert first.calls == [] and len(second.calls) == 1


def test_reservation_without_terminal_after_a_crash_is_a_stop_not_a_repeat(w):
    w.init()

    def crash(*args):
        raise SystemExit('crash during the request')
    with pytest.raises(SystemExit):
        step(w.c, crash)
    fake = Fake(GOOD)
    result = step(w.c, fake, NOW + timedelta(minutes=5))
    assert result['status'] == 'paused' and result['reason'] == 'uncertain_reservation_requires_review'
    assert fake.calls == []


# ---------------------------------------------------------------- resume integrity

def _completed_one(w):
    w.init()
    assert step(w.c, Fake(GOOD))['status'] == 'fetched_ok'
    return w.allowed[0]


RESUME_DEFECTS = {
    'raw_bytes_changed': lambda w, d: (w.root / 'raw' / ('ifs-' + d + '.json')).write_bytes(b'bad'),
    'raw_extra_file': lambda w, d: (w.root / 'raw' / 'extra.json').write_bytes(b'x'),
    'raw_directory_symlink': lambda w, d: (shutil.rmtree(w.root / 'raw'),
                                           (w.root / 'raw').symlink_to(w.parent / 'raw', True)),
    'failures_extra_file': lambda w, d: (w.root / 'failures' / 'x.bin').write_bytes(b'x'),
    'contract_changed': lambda w, d: (w.root / 'contract.json').write_bytes(b'{}'),
    'authorization_changed': lambda w, d: (w.root / 'authorization.txt').write_bytes(b'other'),
    'policy_changed': lambda w, d: (w.root / 'execution-policy.json').write_bytes(b'{}'),
    'state_schema_two': lambda w, d: w.save({**w.state(), 'schema': 2}),
    'event_day_swapped': lambda w, d: w.save({**w.state(), 'events': [
        {**w.state()['events'][0], 'day': w.allowed[1]}]}),
    'event_weight_zero': lambda w, d: w.save({**w.state(), 'events': [
        {**w.state()['events'][0], 'weight': 0}]}),
    'inflight_after_complete': lambda w, d: w.save({**w.state(), 'inflight': {
        'day': w.allowed[1], 'request_sha256': 'c' * 64}}),
    'blank_pause': lambda w, d: w.save({**w.state(), 'paused': ''}),
    'stop_receipt_without_pause': lambda w, d: (w.root / 'failures' / (d + '.stop.json')).write_bytes(b'{}'),
    'clock_ahead_of_now': lambda w, d: w.save({**w.state(), 'last_seen': iso(NOW + timedelta(days=1))}),
    'terminal_receipt_mismatch': lambda w, d: w.save({**w.state(), 'terminal': {d: {
        **w.state()['terminal'][d], 'sha256': 'd' * 64}}}),
}


@pytest.mark.parametrize('defect', sorted(RESUME_DEFECTS))
def test_resume_refuses_damaged_or_inconsistent_census_without_a_request(w, defect):
    day = _completed_one(w)
    RESUME_DEFECTS[defect](w, day)
    if defect == 'clock_ahead_of_now':
        # The clock refusal now persists a sticky STOP at the last valid saved time.
        assert step(w.c, forbidden, NOW + timedelta(minutes=10))['status'] == 'paused'
        assert w.state()['paused'] and len(w.state()['events']) == 1
    else:
        with pytest.raises((ValueError, OSError)):
            step(w.c, forbidden, NOW + timedelta(minutes=10))


# ---------------------------------------------------------------- locks and concurrency

@pytest.mark.parametrize('lock', ['controller.lock', 'worker.lock'])
def test_parent_locks_exclude_initialize_and_step(w, lock):
    with (w.parent / lock).open('r+b') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(CacheRefusal):
            w.init()
    assert not w.root.exists()
    w.init()
    with (w.parent / lock).open('r+b') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(CacheRefusal):
            step(w.c, forbidden)


# ---------------------------------------------------------------- budgets

def test_gap_expenses_are_charged_in_every_window(w):
    recent = NOW - timedelta(seconds=10)
    for event in [e for e in w.p_events if e['kind'] == 'attempt'][-18:]:
        event['at'] = event['reserved_at'] = iso(recent)
    w.g_events[0]['at'] = w.g_events[0]['reserved_at'] = iso(recent)
    w.g_last = recent
    w.pin()
    w.init()
    result = step(w.c, forbidden)
    assert result['status'] == 'quota_wait'
    assert datetime.fromisoformat(result['not_before']) == NOW + timedelta(seconds=50)


@pytest.mark.parametrize('count,age,window,waits', [
    (19, 59, 60, True), (19, 60, 60, False),
    (190, 3599, 3600, True), (190, 3600, 3600, False),
    (333, 86399, 86400, True), (333, 86400, 86400, False),
])
def test_rolling_windows_are_strict_at_their_boundaries(w, count, age, window, waits):
    for event in [e for e in w.p_events if e['kind'] == 'attempt'][-count:]:
        event['at'] = event['reserved_at'] = iso(NOW - timedelta(seconds=age))
    w.pin()
    w.init()
    if waits:
        result = step(w.c, forbidden)
        assert result['status'] == 'quota_wait'
        assert datetime.fromisoformat(result['not_before']) == NOW + timedelta(seconds=window - age)
    else:
        assert step(w.c, Fake(GOOD))['status'] == 'fetched_ok'


def test_utc_midnight_does_not_reset_the_day_budget(w):
    hour_ago = NOW - timedelta(hours=1)
    for event in [e for e in w.p_events if e['kind'] == 'attempt'][-333:]:
        event['at'] = event['reserved_at'] = iso(hour_ago)
    w.pin()
    w.init()
    after_midnight = datetime(2026, 10, 26, 0, 5, tzinfo=timezone.utc)
    result = step(w.c, forbidden, after_midnight)
    assert result['status'] == 'quota_wait'
    assert datetime.fromisoformat(result['not_before']) > after_midnight


def test_combined_ceiling_refuses_before_reservation(w, monkeypatch):
    # Same reviewed numbers, lowered ceiling: the 487th combined attempt must be refused.
    monkeypatch.setitem(m.COMBINED_LIMIT, 'attempts', 486)
    w.pin()
    w.init()
    with pytest.raises(CacheRefusal):
        step(w.c, forbidden)
    assert w.state()['events'] == []


# ---------------------------------------------------------------- full run and ancestors untouched

def test_all_111_outcomes_complete_without_a_112th_request_and_ancestors_unchanged(w):
    before = w.ancestors()
    w.init()
    script = [unavailable(d) if i % 2 else GOOD for i, d in enumerate(w.allowed)]
    fake = Fake(*script)
    for index in range(111):
        result = step(w.c, fake, NOW + timedelta(seconds=4 * index))
        assert result['status'] in ('fetched_ok', 'recorded_run_unavailable')
    result = step(w.c, forbidden, NOW + timedelta(seconds=4 * 111))
    assert result == {'status': m.COMPLETE, 'network_requests': 0, 'automatic_retries': 0}
    assert len(fake.calls) == 111
    assert [c[0] for c in fake.calls] == [w.url(d) for d in w.allowed]
    state = w.state()
    assert len(state['terminal']) == 111 and state['inflight'] is None and state['paused'] is None
    assert w.ancestors() == before


def test_publication_is_calendar_and_counts_only_with_pairs_not_computed(w, monkeypatch):
    monkeypatch.setattr(CacheWorker, 'validate', lambda self_, raw, request: None)
    w.init()
    fake = Fake(GOOD, unavailable(w.allowed[1]), GOOD)
    for index in range(3):
        step(w.c, fake, NOW + timedelta(seconds=4 * index))
    pub = w.c.publication(now=NOW + timedelta(hours=1))
    assert pub['status'] == 'census_incomplete'
    assert len(pub['calendar']) == 607
    assert pub['status_counts'] == {'fetched_ok': 494 + 2, 'recorded_run_unavailable': 1 + 1,
                                    'reserved_or_stop': 1, 'unrequested': 108}
    assert pub['valid_runs'] == 494 + 2 and pub['invalid_runs'] == 0
    assert pub['pair_counts'] == {'status': 'not_computed', 'reason': m.NOT_COMPUTED}
    assert pub['declarations'] == {'census_v1_declared': False, 'strict607_declared': False,
                                   'live_m3_declared': False}
    text = json.dumps(pub)
    assert 'good' not in text and 'temperature' not in text


# ---------------------------------------------------------------- pair counting

def test_pair_counts_keep_zero_and_incomplete_cells_and_need_actuals():
    day = date(2025, 6, 1)
    assert m.count_pairs([day], None) == {'status': 'not_computed', 'reason': m.NOT_COMPUTED}
    cells = m.count_pairs([day], {})['cells']
    assert len(cells) == 12 * len(OPENMETEO_POINTS_V1) * 7
    assert any(c['eligible_valid_runs'] == 0 and c['pairs'] == 0 for c in cells)
    eligible_cells = [c for c in cells if c['eligible_valid_runs'] == 1]
    assert eligible_cells and all(c['pairs'] == 0 and c['incomplete'] == 1 for c in eligible_cells)
    actuals = {(p.code, (day + timedelta(days=lead)).isoformat()): 24
               for p in OPENMETEO_POINTS_V1 for lead in range(1, 8)}
    assert all(c['pairs'] == c['eligible_valid_runs'] for c in m.count_pairs([day], actuals)['cells'])
    below = {k: 18 for k in actuals}
    assert all(c['pairs'] == 0 for c in m.count_pairs([day], below)['cells'])


def test_365_day_window_edges_are_exact():
    origin = m.origins()[0]
    start = origin - timedelta(days=365)
    inside, outside = start.date(), start.date() - timedelta(days=1)

    def oracle(issue, lead):
        cutoff = datetime.combine(issue, datetime.min.time(), timezone.utc) + timedelta(
            hours=23, minutes=59, seconds=59)
        target_end = datetime.combine(issue + timedelta(days=lead + 1), datetime.min.time(), timezone.utc)
        return start <= cutoff < origin and target_end <= origin
    for issue in (inside, outside):
        assert m.eligible(issue, origin, 1) == oracle(issue, 1)
    assert m.eligible(inside, origin, 1) is True
    assert m.eligible(outside, origin, 1) is False


# ---------------------------------------------------------------- policy strictness

def _set(policy, path, value):
    target = policy
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


POLICY_DEFECTS = {
    'missing_budget': lambda p: p.pop('budget'),
    'unknown_field': lambda p: p.update({'extra': 1}),
    'schema_bool': lambda p: _set(p, ['schema'], True),
    'wrong_id': lambda p: _set(p, ['id'], 'other'),
    'commit_mismatch': lambda p: _set(p, ['execution_commit'], 'c' * 40),
    'root_changed': lambda p: _set(p, ['root'], '/elsewhere'),
    'budget_units_per_attempt': lambda p: _set(p, ['budget', 'units_per_attempt'], 22),
    'transport_retries': lambda p: _set(p, ['transport', 'automatic_retries'], 1),
    'alternate_endpoint': lambda p: _set(p, ['transport', 'alternate_endpoints'], ['x']),
    'terminal_rule_substring': lambda p: _set(p, ['terminal_error_rule', 'substring_match'], True),
    'publication_extra_key': lambda p: _set(p, ['publication_scope', 'raw_values'], True),
    'excluded_missing_0806': lambda p: _set(p, ['excluded_dates'], [PROBE_DAY]),
    'calendar_includes_0806': lambda p: _set(p, ['allowed_dates'], [HIST] + p['allowed_dates']),
    'bundle_pin_extra': lambda p: _set(p, ['bundle_pins', 'extra.json'], '3' * 64),
    'ancestor_extra': lambda p: _set(p, ['ancestor_pins', 'extra'], 1),
    'authorization_not_sha': lambda p: _set(p, ['authorization_sha256'], 'xyz'),
}


@pytest.mark.parametrize('defect', sorted(POLICY_DEFECTS))
def test_execution_policy_schema_is_strict(w, defect):
    policy = json.loads(json.dumps(w.policy_for()))
    POLICY_DEFECTS[defect](policy)
    raw = canonical(policy)
    with pytest.raises(CacheRefusal):
        m.CensusCollector(w.parent, w.gap, w.root, raw, digest(raw), code_commit=CODE)


def test_policy_pin_must_match_the_bytes_supplied(w):
    with pytest.raises(CacheRefusal):
        m.CensusCollector(w.parent, w.gap, w.root, w.policy_raw, '0' * 64, code_commit=CODE)


def test_baseline_mismatch_refuses_initialization(w):
    w.p_events.pop()
    w.pin()
    with pytest.raises(CacheRefusal):
        w.init()


def test_altered_parent_receipt_refuses_initialization(w):
    (w.parent / 'raw' / ('ifs-' + w.completed[0] + '.json')).write_bytes(b'changed')
    w.pin()
    with pytest.raises(CacheRefusal):
        w.init()


def test_cli_refuses_a_wrong_commit_before_reading_inputs_or_requesting(tmp_path):
    result = subprocess.run([sys.executable, '-B', str(SCRIPT), '--preflight', '--parent', str(tmp_path / 'p'),
                             '--gap', str(tmp_path / 'g'), '--root', str(tmp_path / 'c'),
                             '--policy', str(tmp_path / 'missing'), '--policy-sha256', '0' * 64,
                             '--execution-commit', '0' * 40], capture_output=True)
    assert result.returncode == 2
    assert json.loads(result.stderr) == {'status': 'refused', 'error_class': 'CacheRefusal'}
    assert not (tmp_path / 'c').exists()


@pytest.mark.parametrize('defect', [None, 'missing_point', 'missing_hour', 'wrong_unit'])
def test_census_uses_original_weather_validator(w, monkeypatch, defect):
    w.init()
    monkeypatch.setattr(CacheWorker, 'validate', ORIGINAL_VALIDATE)
    spec = importlib.util.spec_from_file_location('census_p0_fixture', Path(__file__).with_name(
        'test_forecasting_replay_p0.py'))
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    request = w.requests[w.allowed[0]]
    protocol = {'points': [{'code': p.code, 'lat': p.lat, 'lon': p.lon}
                           for p in OPENMETEO_POINTS_V1],
                'forecast_days': 8, 'forecast_min_hours': 24}
    payload = fixture.payload(protocol, {'kind': 'forecast', 'origin': request['origin']})
    if defect == 'missing_point':
        payload.pop()
    elif defect == 'missing_hour':
        payload[0]['hourly']['time'].pop()
        payload[0]['hourly']['temperature_2m'].pop()
    elif defect == 'wrong_unit':
        payload[0]['hourly_units']['temperature_2m'] = 'F'
    fake = Fake(canonical(payload))
    result = step(w.c, fake)
    assert result['status'] == ('fetched_ok' if defect is None else 'paused')
    assert len(fake.calls) == 1 and len(w.state()['events']) == 1
    assert len(w.state()['terminal']) == (1 if defect is None else 0)
    if defect is not None:
        assert step(w.c, forbidden, NOW + timedelta(seconds=10))['status'] == 'paused'


@pytest.mark.parametrize('field,value', [('http_status', 500), ('error_class', 'URLError'),
                                       ('request_sha256', '0' * 64), ('body_bytes', 0),
                                       ('model', 'other'), ('hour', '06'),
                                       ('policy_sha256', '0' * 64)])
def test_resume_checks_every_unavailable_diagnostic_field(w, field, value):
    w.init()
    day = w.allowed[0]
    assert step(w.c, Fake(unavailable(day)))['status'] == 'recorded_run_unavailable'
    path = w.root / 'failures' / (day + '.unavailable.json')
    record = json.loads(path.read_bytes())
    record[field] = value
    path.write_bytes(canonical(record))
    with pytest.raises(CacheRefusal):
        step(w.c, forbidden, NOW + timedelta(seconds=10))


def test_resume_revalidates_weather_even_if_raw_and_journal_hash_changed(w):
    day = _completed_one(w)
    raw = b'{}'
    (w.root / 'raw' / ('ifs-' + day + '.json')).write_bytes(raw)
    state = w.state()
    state['terminal'][day].update(sha256=digest(raw), bytes=len(raw))
    w.save(state)
    with pytest.raises(CacheRefusal):
        step(w.c, forbidden, NOW + timedelta(seconds=10))


def test_offline_preflight_checks_existing_census_receipts(w):
    day = _completed_one(w)
    (w.root / 'raw' / ('ifs-' + day + '.json')).write_bytes(b'corrupt')
    with pytest.raises(CacheRefusal):
        w.c.preflight_offline(w.bundle, now=NOW + timedelta(seconds=10))


def test_publication_refuses_unpinned_actuals(w):
    w.init()
    with pytest.raises(CacheRefusal):
        w.c.publication(now=NOW, actuals={})


@pytest.mark.parametrize('clock', [Clock(NOW, NOW - timedelta(seconds=1)),
                                    Clock(NOW - timedelta(seconds=1)), Clock()])
def test_clock_refusal_is_sticky_even_without_a_reservation(w, clock):
    w.init()
    result = w.c.step(fetch=forbidden, clock=clock, guard=lambda: None)
    assert result['status'] == 'paused' and result['network_requests'] == 0
    assert w.state()['events'] == []
    assert step(w.c, forbidden, NOW + timedelta(seconds=10))['status'] == 'paused'


def test_first_guard_failure_is_sticky_without_charging_or_get(w):
    w.init()
    def bad_guard():
        raise RuntimeError('source changed')
    result = step(w.c, forbidden, guard=bad_guard)
    assert result['status'] == 'paused' and w.state()['events'] == []
    assert step(w.c, forbidden, NOW + timedelta(seconds=10))['status'] == 'paused'


@pytest.mark.parametrize('filename', sorted(m.BUNDLE_FILES))
def test_existing_root_preflight_checks_saved_bundle_not_only_external(w, filename):
    w.init()
    (w.root / filename).write_bytes(b'changed')
    with pytest.raises(CacheRefusal):
        w.c.preflight_offline(w.bundle, now=NOW)


def test_clearing_only_state_pause_does_not_lift_pre_reservation_stop(w):
    w.init()
    def bad_guard():
        raise RuntimeError('source drift')
    assert step(w.c, forbidden, guard=bad_guard)['status'] == 'paused'
    state = w.state()
    state['paused'] = None
    w.save(state)
    assert step(w.c, forbidden, NOW + timedelta(seconds=10))['status'] == 'paused'
