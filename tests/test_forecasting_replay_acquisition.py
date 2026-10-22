"""Bounded controller tests share synthetic journal fixtures, no network."""
from datetime import timedelta
import fcntl
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_spec = importlib.util.spec_from_file_location('controller_fixtures',
    Path(__file__).with_name('test_forecasting_replay_cache_continuation.py'))
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)
completed, authorize, state, NOW, NEW = (
    _fixtures.completed, _fixtures.authorize, _fixtures.state, _fixtures.NOW, _fixtures.NEW)
from app.services.forecasting import replay_acquisition as q


def run(f, pin, **overrides):
    timing = SimpleNamespace(seconds=30, sleeps=[], calls=[], guards=[])
    def sleep(seconds):
        timing.sleeps.append(seconds); timing.seconds += seconds
    def fetch(*args):
        timing.calls.append(args); return b'good'
    def guard(): timing.guards.append(True)
    args = dict(recovery_pin=f.rpin,continuation_pin=pin,code_commit=NEW,max_requests=40,max_seconds=60,
                fetch=fetch,clock=lambda:NOW+timedelta(seconds=timing.seconds),
                monotonic=lambda:float(timing.seconds),sleep=sleep,guard=guard)
    args.update(overrides)
    return q.run_batch(f.root,**args), timing


def test_controller_finishes_without_retry_or_hidden_quota_wait(completed):
    f = completed; _, pin = authorize(f)
    result, timing = run(f,pin)
    assert result['status'] == 'all_downloaded_pending_final_audit'
    assert result['downloaded'] == 2 and result['automatic_retries'] == 0
    assert len(timing.calls) == 2 and timing.sleeps == [4,4]
    assert len(state(f)['completed']) == 5


def test_request_bound_does_not_fetch_next_day(completed):
    f = completed; _, pin = authorize(f)
    result, timing = run(f,pin,max_requests=1)
    assert result == {'status':'batch_request_limit','downloaded':1,'automatic_retries':0}
    assert len(timing.calls) == 1 and not timing.sleeps


def test_time_bound_stops_before_next_request(completed):
    f = completed; _, pin = authorize(f)
    result, timing = run(f,pin,max_seconds=1)
    assert result == {'status':'batch_time_limit','downloaded':1,'automatic_retries':0}
    assert len(timing.calls) == 1


@pytest.mark.parametrize('key,value',[('max_requests',0),('max_requests',41),('max_requests',True),
                                     ('max_seconds',0),('max_seconds',601),('max_seconds',True)])
def test_invalid_batch_bounds_refuse_before_transport(completed,key,value):
    f = completed; _, pin = authorize(f); before = (f.root/'state.json').read_bytes()
    with pytest.raises(ValueError): run(f,pin,**{key:value})
    assert (f.root/'state.json').read_bytes() == before


def test_unactivated_policy_never_starts_acquisition(completed):
    f = completed; before = (f.root/'state.json').read_bytes()
    with pytest.raises(ValueError,match='authorized continuation'): run(f,'0'*64)
    assert (f.root/'state.json').read_bytes() == before


def test_second_controller_is_refused(completed):
    f = completed; _, pin = authorize(f)
    with (f.root/'controller.lock').open('w+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(ValueError,match='already running'): run(f,pin)


@pytest.mark.parametrize('status',['quota_wait','paused','recovered_without_http'])
def test_stop_statuses_are_returned_without_sleep_or_retry(completed,monkeypatch,status):
    f = completed; _, pin = authorize(f)
    worker = q.CacheWorker(f.root,recovery_pin=f.rpin,continuation_pin=pin)
    result = {'status':status,'not_before':'future'}
    monkeypatch.setattr(worker,'step',lambda **kw:result)
    monkeypatch.setattr(q,'CacheWorker',lambda *a,**kw:worker)
    out, timing = run(f,pin)
    assert out['status'] == status and out['downloaded'] == 0
    assert not timing.calls and not timing.sleeps


@pytest.mark.parametrize('at_transport',[False,True])
def test_source_change_refuses_before_http_and_never_retries(completed,at_transport):
    f = completed; _, pin = authorize(f); count = 0
    def guard():
        nonlocal count
        count += 1
        if count == (2 if at_transport else 1): raise ValueError('synthetic changed source')
    if at_transport:
        result,timing = run(f,pin,guard=guard)
        assert result['status'] == 'paused' and not timing.calls
        assert len(state(f)['events']) == 6  # Reserved before transport; conservatively spent.
    else:
        with pytest.raises(ValueError): run(f,pin,guard=guard)
        assert len(state(f)['events']) == 5


def test_controller_lock_releases_after_guard_failure(completed):
    f = completed; _, pin = authorize(f)
    with pytest.raises(ValueError): run(f,pin,guard=lambda:(_ for _ in ()).throw(ValueError()))
    result,_ = run(f,pin,max_requests=1)
    assert result['downloaded'] == 1
