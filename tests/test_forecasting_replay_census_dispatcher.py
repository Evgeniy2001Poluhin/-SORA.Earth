"""Dispatcher integration with the actual census worker, synthetic ancestors only."""
import fcntl
import json
from pathlib import Path
import socket

import pytest

from app.services.forecasting import replay_census_dispatcher as d
from app.services.forecasting.replay_source_gap_contract import canonical
from app.services.forecasting.replay_cache_worker import CacheRefusal, digest
from tests.test_forecasting_replay_source_census import (
    World, NOW, CODE, Fake, GOOD, unavailable, UnexpectedGet,
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    def blocked(*args, **kwargs):
        raise UnexpectedGet('network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    w = World(tmp_path, monkeypatch)
    root = tmp_path / 'dispatch'
    dispatcher = d.CensusDispatcher(w.c, root)
    record = {'schema': 1, 'decision': 'approve:' + d.CENSUS_ID,
              'execution_commit': CODE, 'policy_sha256': w.policy_pin,
              'root': str(w.root), 'journal_root': str(root), 'max_steps': 10,
              'owner_text': 'SYNTHETIC TEST DECISION, NOT A REAL AUTHORIZATION',
              'approved_at': NOW.isoformat()}
    return w, dispatcher, record


def run(env, *, mode='step', steps=1, run_id='one', fake=None, **extra):
    w, dispatcher, record = env
    raw = canonical(record)
    return dispatcher.run(approval_raw=raw, approval_pin=digest(raw), run_id=run_id,
                          mode=mode, steps=steps, clock=lambda: NOW, guard=lambda: None,
                          fetch=fake or Fake(GOOD), bundle=w.bundle if mode == 'initialize' else None,
                          **extra)


@pytest.mark.parametrize('field,value', [('decision', 'draft'), ('execution_commit', 'a' * 40),
                                       ('policy_sha256', '0' * 64), ('root', '/other'),
                                       ('journal_root', '/other'), ('max_steps', 0),
                                       ('max_steps', 11), ('max_steps', True),
                                       ('owner_text', ''), ('schema', True)])
def test_bad_approval_refuses_without_namespace_or_get(env, field, value):
    w, dispatcher, record = env
    record[field] = value
    fake = Fake(GOOD)
    with pytest.raises((ValueError, OSError)):
        run(env, mode='initialize', fake=fake)
    assert fake.calls == [] and not dispatcher.root.exists() and not w.root.exists()


def test_approval_digest_must_be_independent_and_exact(env):
    w, dispatcher, record = env
    with pytest.raises(CacheRefusal):
        dispatcher.run(approval_raw=canonical(record), approval_pin='0' * 64,
                       run_id='one', mode='initialize', steps=1, clock=lambda: NOW,
                       guard=lambda: None, bundle=w.bundle)
    assert not dispatcher.root.exists() and not w.root.exists()


def test_initialize_is_zero_get_and_stores_start_and_final_binding(env):
    w, dispatcher, record = env
    before = w.ancestors()
    fake = Fake()
    result = run(env, mode='initialize', fake=fake)
    assert result['status'] == 'census_initialized' and result['transport_calls'] == 0
    assert result['start_sha256'] == digest((dispatcher.root / 'one/start.json').read_bytes())
    assert fake.calls == [] and w.ancestors() == before


def test_step_has_durable_start_inside_fetch_and_exactly_one_request(env):
    w, dispatcher, record = env
    run(env, mode='initialize', run_id='init')
    def fetch(*args):
        assert (dispatcher.root / 'one/start.json').exists()
        assert (dispatcher.root / 'one/step-00-start.json').exists()
        assert not (dispatcher.root / 'one/result.json').exists()
        assert w.state()['inflight'] and len(w.state()['events']) == 1
        return GOOD
    raw = canonical(record)
    result = dispatcher.run(approval_raw=raw, approval_pin=digest(raw), run_id='one',
                            mode='step', steps=1, clock=lambda: NOW, guard=lambda: None, fetch=fetch)
    assert result['transport_calls'] == 1 and result['status'] == 'fetched_ok'
    assert (dispatcher.root / 'one/step-00-result.json').exists()


def test_quota_wait_ends_dispatch_without_sleep_or_retry(env, monkeypatch):
    import time
    w, dispatcher, record = env
    run(env, mode='initialize', run_id='init')
    monkeypatch.setattr(time, 'sleep', lambda *a: pytest.fail('no sleeping'))
    fake = Fake(GOOD)
    result = run(env, steps=10, fake=fake)
    assert len(fake.calls) == result['transport_calls'] == 1
    assert result['status'] == 'quota_wait' and len(result['results']) == 2


def test_recorded_unavailable_also_obeys_worker_spacing(env):
    w, dispatcher, record = env
    run(env, mode='initialize', run_id='init')
    fake = Fake(unavailable(w.allowed[0]))
    result = run(env, steps=10, fake=fake)
    assert result['transport_calls'] == 1 and result['status'] == 'quota_wait'
    assert result['results'][0]['status'] == 'recorded_run_unavailable'


def test_stop_ends_dispatch_and_does_not_consume_the_next_fake(env):
    w, dispatcher, record = env
    run(env, mode='initialize', run_id='init')
    fake = Fake(TimeoutError('stop'), GOOD)
    result = run(env, steps=10, fake=fake)
    assert result['status'] == 'paused' and result['transport_calls'] == 1
    assert len(result['results']) == 1
    assert len(fake.calls) == 1 and len(fake.outcomes) == 1


def test_run_id_cannot_be_reused_even_after_success(env):
    w, dispatcher, record = env
    run(env, mode='initialize')
    with pytest.raises(CacheRefusal):
        run(env, mode='initialize')


def test_crash_blocks_different_run_id_without_repeating_transport(env):
    w, dispatcher, record = env
    run(env, mode='initialize', run_id='init')
    def crash(*args):
        raise SystemExit('simulated process loss')
    raw = canonical(record)
    with pytest.raises(SystemExit):
        dispatcher.run(approval_raw=raw, approval_pin=digest(raw), run_id='one', mode='step',
                       steps=1, clock=lambda: NOW, guard=lambda: None, fetch=crash)
    fake = Fake(GOOD)
    with pytest.raises(OSError):
        run(env, run_id='different', fake=fake)
    assert fake.calls == [] and len(w.state()['events']) == 1


def test_dispatch_lock_excludes_a_second_invocation(env):
    w, dispatcher, record = env
    run(env, mode='initialize', run_id='init')
    with (dispatcher.root / 'dispatch.lock').open('r+b') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError):
            run(env)


def test_final_result_write_failure_leaves_a_no_repeat_barrier(env, monkeypatch):
    w, dispatcher, record = env
    run(env, mode='initialize', run_id='init')
    original = d.write_new
    def fail(path, raw):
        if path.name == 'result.json':
            raise OSError('disk failure')
        return original(path, raw)
    monkeypatch.setattr(d, 'write_new', fail)
    with pytest.raises(OSError):
        run(env, fake=Fake(GOOD))
    monkeypatch.setattr(d, 'write_new', original)
    fake = Fake(GOOD)
    with pytest.raises(OSError):
        run(env, run_id='later', fake=fake)
    assert fake.calls == [] and len(w.state()['events']) == 1


@pytest.mark.parametrize('steps', [0, 11, True])
def test_invocation_bound_is_checked_before_admission(env, steps):
    with pytest.raises(CacheRefusal):
        run(env, mode='initialize', steps=steps)


def test_dispatch_journal_must_not_overlap_acquisition_roots(env):
    w, dispatcher, record = env
    for path in [w.parent, w.parent / 'dispatch', w.gap, w.root / 'dispatch']:
        with pytest.raises(CacheRefusal):
            d.CensusDispatcher(w.c, path)


def test_multiple_steps_reach_the_approved_bound_without_an_extra_get(env):
    from datetime import timedelta
    w, dispatcher, record = env
    run(env, mode='initialize', run_id='init')
    ticks = {'n': 0}
    def clock():
        ticks['n'] += 1
        return NOW + timedelta(seconds=4 * ticks['n'])
    fake = Fake(GOOD, GOOD, GOOD, GOOD)
    raw = canonical(record)
    result = dispatcher.run(approval_raw=raw, approval_pin=digest(raw), run_id='batch',
                            mode='step', steps=3, clock=clock, guard=lambda: None, fetch=fake)
    assert result['transport_calls'] == len(fake.calls) == len(result['results']) == 3
    assert len(fake.outcomes) == 1 and len(w.state()['events']) == 3


def test_final_result_failure_after_initialize_also_blocks_different_id(env, monkeypatch):
    w, dispatcher, record = env
    original = d.write_new
    def fail(path, raw):
        if path.name == 'result.json':
            raise OSError('cannot finalize initialization')
        return original(path, raw)
    monkeypatch.setattr(d, 'write_new', fail)
    with pytest.raises(OSError):
        run(env, mode='initialize', run_id='init')
    monkeypatch.setattr(d, 'write_new', original)
    fake = Fake(GOOD)
    with pytest.raises(OSError):
        run(env, run_id='later', fake=fake)
    assert fake.calls == [] and w.state()['events'] == []


def test_guard_failure_after_step_start_keeps_no_get_and_worker_pause(env):
    w, dispatcher, record = env
    run(env, mode='initialize', run_id='init')
    calls = {'n': 0}
    def guard():
        calls['n'] += 1
        if calls['n'] == 2:
            raise RuntimeError('source changed after dispatch admission')
    fake = Fake(GOOD)
    raw = canonical(record)
    result = dispatcher.run(approval_raw=raw, approval_pin=digest(raw), run_id='one',
                            mode='step', steps=1, clock=lambda: NOW, guard=guard, fetch=fake)
    assert result['status'] == 'paused' and result['transport_calls'] == 0
    assert fake.calls == [] and w.state()['paused'] and w.state()['events'] == []


def test_clean_checkout_switch_cannot_change_loaded_execution_identity(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(d, 'exact_source', lambda *a: calls.append(a))
    with pytest.raises(CacheRefusal):
        d.loaded_source(tmp_path, 'b' * 40, loaded_commit='a' * 40, loaded_clean=True)
    assert calls == []
    with pytest.raises(CacheRefusal):
        d.loaded_source(tmp_path, 'a' * 40, loaded_commit='a' * 40, loaded_clean=False)
    assert calls == []
    d.loaded_source(tmp_path, 'a' * 40, loaded_commit='a' * 40, loaded_clean=True)
    assert len(calls) == 1


def test_output_paths_cannot_dirty_execution_checkout(env, tmp_path):
    w, dispatcher, record = env
    source = tmp_path / 'source'
    source.mkdir()
    with pytest.raises(CacheRefusal):
        d.CensusDispatcher(w.c, source / 'journal', execution_root=source)
    assert not (source / 'journal').exists()
    with pytest.raises(CacheRefusal):
        d.CensusDispatcher(w.c, dispatcher.root, execution_root=w.root.parent)
    assert not dispatcher.root.exists()


@pytest.mark.parametrize('defect', ['missing_results', 'excess_calls', 'wrong_policy'])
def test_prior_journal_corruption_cannot_be_read_as_a_completed_run(env, defect):
    w, dispatcher, record = env
    run(env, mode='initialize', run_id='init')
    path = dispatcher.root / 'init' / ('start.json' if defect == 'wrong_policy' else 'result.json')
    value = json.loads(path.read_bytes())
    if defect == 'missing_results':
        value.pop('results')
    elif defect == 'excess_calls':
        value['transport_calls'] = 11
    else:
        value['policy_sha256'] = '0' * 64
    path.write_bytes(canonical(value))
    fake = Fake(GOOD)
    with pytest.raises(CacheRefusal):
        run(env, run_id='later', fake=fake)
    assert fake.calls == []
