"""Synthetic local journals and fake transport only; never opens real cache."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import fcntl
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.forecasting import replay_cache_worker as c
from app.services.forecasting import replay_cache_recovery as r
from app.services.forecasting import replay_cache_continuation as x
from app.services.forecasting.replay_cache_plan import _request

NOW = datetime(2026, 10, 22, 16, tzinfo=timezone.utc)
OLD, RECOVERY, NEW = 'a'*40, 'b'*40, 'c'*40


def forbidden(*args):
    pytest.fail('unexpected transport/retry')


@pytest.fixture
def completed(tmp_path, monkeypatch):
    plan = c.build_plan()
    days = [date(2024, 11, 25)+timedelta(days=i) for i in range(5)]
    plan['requests'] = [_request(d) for d in days]
    plan['probe_requests'], plan['reuse_candidates'] = [], []
    monkeypatch.setattr(c, 'build_plan', lambda: plan)
    monkeypatch.setattr(r, 'stage_dates', lambda: {str(d) for d in days[:3]})
    real_validate = c.CacheWorker.validate
    monkeypatch.setattr(c.CacheWorker, 'validate', lambda self, raw, req: c.need(raw == b'good', 'bad fixture'))
    root = tmp_path/'cache'; raw = c.canonical(plan)
    c.initialize(root, raw, c.digest(raw), prior_units=0, now=NOW, code_commit=OLD)
    def fail(*a):
        raise c.TransportFailure(error_class='URLError', returncode=2)
    assert c.CacheWorker(root).step(now=NOW, fetch=fail, code_commit=OLD)['status'] == 'paused'
    original = (root/'state.json').read_bytes()
    recovery = {'schema':1, 'id':'M3-P1-ONE-RECOVERY-2026-10-22', 'retry_limit':1,
                'failed_day':str(days[0]), 'canonical_ledger':str(root.resolve()),
                'failed_state_sha256':c.digest(original), 'execution_commit':RECOVERY,
                'caps':deepcopy(x.CAPS), 'scientific_changes':False,
                'on_failure':'stop_no_more_retries', 'allowed_missing_dates':[str(d) for d in days[:3]],
                'original_attempts':1, 'max_total_attempts':4, 'max_total_reserved_units':84}
    data = c.canonical(recovery); rpin = c.digest(data)
    r.authorize_once(root, data, rpin, now=NOW+timedelta(seconds=10), code_commit=RECOVERY)
    w = c.CacheWorker(root, recovery_pin=rpin)
    for seconds in (10, 14, 18):
        assert w.step(now=NOW+timedelta(seconds=seconds), fetch=lambda *a:b'good', code_commit=RECOVERY)['status'] == 'downloaded'
    checkpoint = (root/'state.json').read_bytes()
    policy = {'schema':1, 'id':'M3-P1-FULL-CALENDAR-2026-10-22',
              'canonical_ledger':str(root.resolve()), 'checkpoint_sha256':c.digest(checkpoint),
              'recovery_amendment_sha256':rpin, 'execution_commit':NEW,
              'allowed_missing_dates':[str(d) for d in days[3:]], 'max_additional_attempts':2,
              'max_total_attempts':6, 'max_total_reserved_units':126,
              'caps':deepcopy(x.CAPS), 'retries':0, 'scientific_changes':False,
              'on_failure':'stop_no_retries'}
    return SimpleNamespace(root=root, policy=policy, rpin=rpin, checkpoint=checkpoint,
                           plan=plan, real_validate=real_validate)


def authorize(f):
    raw = c.canonical(f.policy); pin = c.digest(raw)
    result = x.authorize_once(f.root, raw, pin, f.rpin, now=NOW+timedelta(seconds=30), code_commit=NEW)
    return result, pin


def worker(f, pin):
    return c.CacheWorker(f.root, recovery_pin=f.rpin, continuation_pin=pin)


def state(f):
    return c.parse_json((f.root/'state.json').read_bytes())


def test_authorize_offline_preserves_complete_checkpoint_and_parent_identity(completed):
    f = completed; result, pin = authorize(f)
    assert result == {'status':'authorized_continuation', 'network_requests':0, 'additional_attempts':2}
    assert (f.root/x.CHECKPOINT).read_bytes() == f.checkpoint
    old = c.parse_json(f.checkpoint); current = state(f)
    for key in ('events', 'completed', 'code_commit', 'plan_sha256', 'manual_recovery'):
        assert current[key] == old[key]
    assert current['calendar_continuation']['policy_sha256'] == pin


def test_all_new_dates_reserve_once_then_stop_and_audit(completed):
    f = completed; _, pin = authorize(f); w = worker(f, pin); calls = []
    old = c.parse_json(f.checkpoint)
    def fetch(*args):
        current = state(f)
        assert current['events'][:len(old['events'])] == old['events']
        assert len(current['events']) == len(old['events'])+len(calls)+1
        event = current['events'][-1]
        assert event['calendar_continuation_sha256'] == pin and event['weight'] == 21
        assert 'attempt_ordinal' not in event
        assert current['inflight']['day'] not in old['completed']
        calls.append(current['inflight']['day']); return b'good'
    for second in (30, 34):
        assert w.step(now=NOW+timedelta(seconds=second), fetch=fetch, code_commit=NEW)['status'] == 'downloaded'
    assert calls == f.policy['allowed_missing_dates']
    assert w.step(now=NOW+timedelta(seconds=38), fetch=forbidden, code_commit=NEW)['status'] == 'all_downloaded_pending_final_audit'
    assert w.audit(now=NOW+timedelta(seconds=38))['ready_for_review']
    assert len(state(f)['events']) == 7 and sum(e['weight'] for e in state(f)['events']) == 126


@pytest.mark.parametrize('field,value', [
    ('schema',True), ('retries',True), ('retries',1), ('id','other'), ('canonical_ledger','/other'),
    ('checkpoint_sha256','0'*64), ('recovery_amendment_sha256','0'*64), ('execution_commit','bad'),
    ('allowed_missing_dates',['2024-11-25']), ('allowed_missing_dates',['2024-11-29','2024-11-28']),
    ('max_additional_attempts',3), ('max_total_attempts',7), ('max_total_reserved_units',999),
    ('caps',{'rolling60s':999}), ('scientific_changes',True), ('on_failure','retry'),
])
def test_bad_policy_refuses_without_ledger_mutation(completed, field, value):
    f = completed; f.policy[field] = value
    with pytest.raises(ValueError):
        authorize(f)
    assert (f.root/'state.json').read_bytes() == f.checkpoint
    assert not (f.root/x.CHECKPOINT).exists()


def test_wrong_external_pin_code_or_unknown_field_refuses(completed):
    f = completed; raw = c.canonical(f.policy)
    with pytest.raises(ValueError):
        x.authorize_once(f.root, raw, '0'*64, f.rpin, now=NOW+timedelta(seconds=30), code_commit=NEW)
    with pytest.raises(ValueError):
        x.authorize_once(f.root, raw, c.digest(raw), f.rpin, now=NOW+timedelta(seconds=30), code_commit=OLD)
    f.policy['hidden_retry'] = True
    with pytest.raises(ValueError):
        authorize(f)
    assert (f.root/'state.json').read_bytes() == f.checkpoint


def test_both_external_pins_and_new_execution_commit_required(completed):
    f = completed; _, pin = authorize(f)
    for w in (c.CacheWorker(f.root), c.CacheWorker(f.root,recovery_pin=f.rpin),
              c.CacheWorker(f.root,continuation_pin=pin)):
        with pytest.raises(ValueError):
            w.step(now=NOW+timedelta(seconds=31), fetch=forbidden, code_commit=NEW)
    with pytest.raises(ValueError, match='execution code'):
        worker(f,pin).step(now=NOW+timedelta(seconds=31), fetch=forbidden, code_commit=RECOVERY)


def test_failure_and_reauthorization_never_retry_or_clear_pause(completed):
    f = completed; _, pin = authorize(f); w = worker(f,pin)
    def fail(*a):
        raise c.TransportFailure(429, error_class='HTTPError', returncode=2)
    assert w.step(now=NOW+timedelta(seconds=30), fetch=fail, code_commit=NEW)['status'] == 'paused'
    before = (f.root/'state.json').read_bytes()
    assert authorize(f)[0]['status'] == 'already_authorized'
    assert (f.root/'state.json').read_bytes() == before
    assert w.step(now=NOW+timedelta(days=2), fetch=forbidden, code_commit=NEW)['status'] == 'paused'
    assert len(state(f)['events']) == 6


@pytest.mark.parametrize('raw_saved', [False, True])
def test_crash_recovers_only_pinned_bytes_never_repeats_http(completed, raw_saved, monkeypatch):
    f = completed; _, pin = authorize(f); w = worker(f,pin); complete = w.complete
    def crash(*a,**kw):
        raise KeyboardInterrupt()
    if raw_saved:
        monkeypatch.setattr(w,'complete',crash)
    with pytest.raises(KeyboardInterrupt):
        w.step(now=NOW+timedelta(seconds=30), fetch=(lambda *a:b'good') if raw_saved else crash, code_commit=NEW)
    monkeypatch.setattr(w,'complete',complete)
    result = w.step(now=NOW+timedelta(seconds=34), fetch=forbidden, code_commit=NEW)
    assert result['status'] == ('recovered_without_http' if raw_saved else 'paused')
    assert len(state(f)['events']) == 6


def test_completion_spacing_and_clock_rollback_remain_enforced(completed):
    f = completed; _, pin = authorize(f); w = worker(f,pin)
    clock = iter([NOW+timedelta(seconds=30), NOW+timedelta(seconds=50)])
    assert w.step(now=NOW+timedelta(seconds=30), fetch=lambda *a:b'good', code_commit=NEW, clock=lambda:next(clock))['status'] == 'downloaded'
    with pytest.raises(ValueError, match='rollback'):
        w.step(now=NOW+timedelta(seconds=49), fetch=forbidden, code_commit=NEW)
    assert w.step(now=NOW+timedelta(seconds=51), fetch=forbidden, code_commit=NEW) == {
        'status':'quota_wait','not_before':(NOW+timedelta(seconds=54)).isoformat()}


@pytest.mark.parametrize('cap,seconds', [(400,60), (4000,3600), (7000,86400)])
def test_continuation_keeps_seeded_rolling_budget_and_exact_expiry(completed, cap, seconds):
    f = completed
    # Rebuild only this synthetic fixture's quota seed and dependent pins BEFORE
    # the continuation checkpoint is frozen; no real ledger is edited by tests.
    original = c.parse_json((f.root/r.BACKUP).read_bytes())
    original['events'][0]['weight'] = cap-84
    raw_original = c.canonical(original)
    (f.root/r.BACKUP).write_bytes(raw_original)
    recovery = c.parse_json((f.root/r.AMENDMENT).read_bytes())
    recovery['failed_state_sha256'] = c.digest(raw_original)
    recovery['max_total_reserved_units'] = cap
    raw_recovery = c.canonical(recovery); f.rpin = c.digest(raw_recovery)
    (f.root/r.AMENDMENT).write_bytes(raw_recovery)
    current = state(f)
    current['events'][0] = original['events'][0]
    current['events'][2]['recovery_amendment_sha256'] = f.rpin
    current['manual_recovery']['amendment_sha256'] = f.rpin
    c.save_state(f.root,current)
    f.checkpoint = (f.root/'state.json').read_bytes()
    f.policy.update(checkpoint_sha256=c.digest(f.checkpoint), recovery_amendment_sha256=f.rpin,
                    max_total_reserved_units=cap+42)
    _, pin = authorize(f); w = worker(f,pin)
    assert w.step(now=NOW+timedelta(seconds=30), fetch=forbidden, code_commit=NEW) == {
        'status':'quota_wait', 'not_before':(NOW+timedelta(seconds=seconds)).isoformat()}
    assert len(state(f)['events']) == 5
    assert w.step(now=NOW+timedelta(seconds=seconds), fetch=lambda *a:b'good', code_commit=NEW)['status'] == 'downloaded'


def test_incomplete_slice_cannot_be_extended(completed):
    f = completed; current = state(f)
    current['completed'].pop('2024-11-27'); current['events'].pop()
    c.save_state(f.root,current)
    f.checkpoint = (f.root/'state.json').read_bytes()
    f.policy['checkpoint_sha256'] = c.digest(f.checkpoint)
    with pytest.raises(ValueError,match='first slice not complete'): authorize(f)
    assert (f.root/'state.json').read_bytes() == f.checkpoint


def test_new_import_still_refuses_before_opening_source(completed, tmp_path):
    f = completed; _, pin = authorize(f); before = (f.root/'state.json').read_bytes()
    with pytest.raises(ValueError, match='new imports.*recovery'):
        worker(f,pin).import_one('2024-11-28',tmp_path/'nonexistent','0'*64,now=NOW+timedelta(seconds=31))
    assert (f.root/'state.json').read_bytes() == before


@pytest.mark.parametrize('changed', ['events', 'receipt', 'code_commit', 'manual_recovery', 'checkpoint'])
def test_frozen_parent_cannot_be_changed(completed, changed):
    f = completed; _, pin = authorize(f); current = state(f)
    if changed == 'checkpoint':
        (f.root/x.CHECKPOINT).write_bytes(b'{}')
    else:
        if changed == 'events': current['events'][0]['weight'] = 1
        if changed == 'receipt': current['completed']['2024-11-25']['source']['extra'] = True
        if changed == 'code_commit': current['code_commit'] = NEW
        if changed == 'manual_recovery': current['manual_recovery']['enabled_at'] = NOW.isoformat()
        c.save_state(f.root,current)
    with pytest.raises(ValueError):
        worker(f,pin).step(now=NOW+timedelta(seconds=31), fetch=forbidden, code_commit=NEW)


def test_authorization_disk_failure_can_resume_offline_without_reset(completed, monkeypatch):
    f = completed; save = x.save_state
    def fail(*a): raise OSError('synthetic disk error')
    monkeypatch.setattr(x,'save_state',fail)
    with pytest.raises(OSError): authorize(f)
    assert (f.root/'state.json').read_bytes() == f.checkpoint
    assert (f.root/x.CHECKPOINT).read_bytes() == f.checkpoint
    monkeypatch.setattr(x,'save_state',save)
    assert authorize(f)[0]['status'] == 'authorized_continuation'


def test_lock_and_preexisting_unreceipted_raw_refuse(completed):
    f = completed
    with (f.root/'worker.lock').open('rb') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(ValueError,match='busy'): authorize(f)
    (f.root/'raw/ifs-2024-11-28.json').write_bytes(b'good')
    with pytest.raises(ValueError,match='unreceipted'): authorize(f)
    assert (f.root/'state.json').read_bytes() == f.checkpoint


def test_real_point_hour_validator_works_for_new_run(completed, monkeypatch):
    f = completed; _, pin = authorize(f); w = worker(f,pin)
    request = f.plan['requests'][3]
    start = datetime.fromisoformat(request['origin']).replace(hour=0,minute=0,second=0)
    times = [(start+timedelta(hours=h)).strftime('%Y-%m-%dT%H:%M') for h in range(192)]
    raw = c.canonical([{'latitude':p.lat,'longitude':p.lon,'timezone':'UTC','utc_offset_seconds':0,
                        'hourly_units':{'time':'iso8601','temperature_2m':'°C'},
                        'hourly':{'time':times,'temperature_2m':[10.0]*192}} for p in c.OPENMETEO_POINTS_V1])
    monkeypatch.setattr(c.CacheWorker,'validate',f.real_validate)
    assert w.step(now=NOW+timedelta(seconds=30), fetch=lambda *a:raw, code_commit=NEW)['status'] == 'downloaded'


def test_cli_authorization_is_offline_and_source_confirmation_required(completed, tmp_path, monkeypatch, capsys):
    f = completed
    spec = importlib.util.spec_from_file_location('continuation_cli',Path(__file__).parents[1]/'scripts/forecast_replay_cache_worker.py')
    cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
    class Clock:
        @staticmethod
        def now(zone): return NOW+timedelta(seconds=30)
    monkeypatch.setattr(cli,'datetime',Clock)
    monkeypatch.setattr(cli,'frozen_head',lambda:NEW)
    monkeypatch.setattr(cli,'bounded_fetch',forbidden)
    raw = c.canonical(f.policy); file = tmp_path/'policy.json'; file.write_bytes(raw)
    args = ['authorize-continuation',str(f.root),'--policy',str(file),'--policy-sha256',c.digest(raw),
            '--recovery-sha256',f.rpin,'--confirm-code-commit',NEW]
    assert cli.main(args[:-1]+[OLD]) == 2
    assert cli.main(args) == 0
    assert c.parse_json(capsys.readouterr().out.encode())['network_requests'] == 0
    assert cli.main(['status',str(f.root),'--recovery-sha256',f.rpin,'--continuation-sha256',c.digest(raw)]) == 0
