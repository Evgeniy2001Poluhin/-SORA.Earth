"""Synthetic multi-amendment journals; real transport and owner cache never opened."""
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
import fcntl
import importlib.util

import pytest

from app.services.forecasting import replay_cache_worker as c
from app.services.forecasting import replay_cache_recovery as first
from app.services.forecasting import replay_cache_continuation as calendar
from app.services.forecasting import replay_calendar_recovery as retry
from app.services.forecasting import replay_transport_recovery as transport
from app.services.forecasting import replay_post_retry_continuation as r
from app.services.forecasting import replay_acquisition as q
from app.services.forecasting.replay_cache_plan import _request


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


prior = module('post_retry_fixtures', Path(__file__).with_name('test_forecasting_replay_cache_continuation.py'))
NOW, OLD, NEW = prior.NOW, 'e' * 40, 'f' * 40


def forbidden(*a, **kw):
    pytest.fail('unexpected HTTP or retry')


def state(f):
    return c.parse_json((f.root / 'state.json').read_bytes())


@pytest.fixture
def settled(tmp_path, monkeypatch):
    # Larger calendar than the parent test fixture: three dates remain after
    # completing both independently authorized historical retries.
    plan = c.build_plan()
    days = [date(2024, 11, 25) + timedelta(days=i) for i in range(8)]
    plan['requests'] = [_request(d) for d in days]
    plan['probe_requests'], plan['reuse_candidates'] = [], []
    monkeypatch.setattr(c, 'build_plan', lambda: plan)
    monkeypatch.setattr(first, 'stage_dates', lambda: {str(d) for d in days[:3]})
    monkeypatch.setattr(c.CacheWorker, 'validate', lambda self, raw, req: c.need(raw == b'good', 'bad fixture'))
    root = tmp_path / 'cache'
    raw = c.canonical(plan)
    c.initialize(root, raw, c.digest(raw), prior_units=0, now=NOW, code_commit=prior.OLD)
    def failure(*a):
        raise c.TransportFailure(error_class='URLError', returncode=2)
    assert c.CacheWorker(root).step(now=NOW, fetch=failure, code_commit=prior.OLD)['status'] == 'paused'
    checkpoint = (root / 'state.json').read_bytes()
    p = {'schema': 1, 'id': 'M3-P1-ONE-RECOVERY-2026-10-22', 'retry_limit': 1,
         'failed_day': str(days[0]), 'canonical_ledger': str(root.resolve()),
         'failed_state_sha256': c.digest(checkpoint), 'execution_commit': prior.RECOVERY,
         'caps': deepcopy(r.CAPS), 'scientific_changes': False, 'on_failure': 'stop_no_more_retries',
         'allowed_missing_dates': [str(d) for d in days[:3]], 'original_attempts': 1,
         'max_total_attempts': 4, 'max_total_reserved_units': 84}
    raw = c.canonical(p); rpin = c.digest(raw)
    first.authorize_once(root, raw, rpin, now=NOW+timedelta(seconds=10), code_commit=prior.RECOVERY)
    w = c.CacheWorker(root, recovery_pin=rpin)
    for sec in (10, 14, 18):
        assert w.step(now=NOW+timedelta(seconds=sec), fetch=lambda *a:b'good', code_commit=prior.RECOVERY)['status'] == 'downloaded'
    checkpoint = (root / 'state.json').read_bytes()
    p = {'schema': 1, 'id': 'M3-P1-FULL-CALENDAR-2026-10-22', 'canonical_ledger': str(root.resolve()),
         'checkpoint_sha256': c.digest(checkpoint), 'recovery_amendment_sha256': rpin,
         'execution_commit': prior.NEW, 'allowed_missing_dates': [str(d) for d in days[3:]],
         'max_additional_attempts': 5, 'max_total_attempts': 9, 'max_total_reserved_units': 189,
         'caps': deepcopy(r.CAPS), 'retries': 0, 'scientific_changes': False, 'on_failure': 'stop_no_retries'}
    f = SimpleNamespace(root=root, policy=p, rpin=rpin, plan=plan)
    failed = module('post_calendar_fixtures', Path(__file__).with_name('test_forecasting_replay_calendar_recovery.py'))
    failed.failed.__wrapped__(f, monkeypatch)
    f.retry['max_total_attempts'] += 3
    f.retry['max_total_reserved_units'] += 63
    tf = module('post_transport_fixtures', Path(__file__).with_name('test_forecasting_replay_transport_recovery.py'))
    tf.transport_failed.__wrapped__(f)
    _, f.tpin = tf.authorize(f)
    assert tf.worker(f, f.tpin).step(now=NOW+timedelta(seconds=50), fetch=lambda *a:b'good', code_commit=OLD)['status'] == 'downloaded'
    f.checkpoint = (root / 'state.json').read_bytes()
    f.parents = (f.rpin, f.cpin, f.calendar_pin, f.tpin)
    s = state(f); missing = sorted(set(str(d) for d in days) - s['completed'].keys())
    f.post = {'schema': 1, 'id': 'M3-P1-POST-RETRY-CONTINUATION', 'canonical_ledger': str(root.resolve()),
              'checkpoint_sha256': c.digest(f.checkpoint), 'parent_pins': list(f.parents),
              'execution_commit': NEW, 'allowed_missing_dates': missing, 'max_additional_attempts': len(missing),
              'max_total_attempts': sum(e['kind']=='attempt' for e in s['events'])+len(missing),
              'max_total_reserved_units': sum(e['weight'] for e in s['events'])+21*len(missing),
              'retries': 0, 'caps': deepcopy(r.CAPS), 'scientific_changes': False, 'on_failure': 'stop_no_retries'}
    return f


def authorize(f, sec=60):
    raw = c.canonical(f.post); pin = c.digest(raw)
    return r.authorize_once(f.root, raw, pin, *f.parents, now=NOW+timedelta(seconds=sec), code_commit=NEW), pin


def worker(f, pin):
    return r.PostRetryWorker(f.root, *f.parents, pin)


def test_remaining_dates_once_in_order_then_offline_audit(settled):
    f = settled; result, pin = authorize(f)
    assert result == {'status':'authorized_post_retry_continuation','network_requests':0,'additional_attempts':3,'retries':0}
    assert (f.root/r.CHECKPOINT).read_bytes() == f.checkpoint
    before = c.parse_json(f.checkpoint)
    calls = []
    def fetch(*a):
        s = state(f); e = s['events'][-1]
        assert s['events'][:len(before['events'])] == before['events']
        assert e['day'] not in {e['day'] for e in before['events'] if e['kind']=='attempt'}
        assert 'attempt_ordinal' not in e and 'recovery_amendment_sha256' not in e
        calls.append(e['day']); return b'good'
    w = worker(f,pin)
    for sec in (60,64,68):
        assert w.step(now=NOW+timedelta(seconds=sec),fetch=fetch,code_commit=NEW)['status']=='downloaded'
    assert calls == f.post['allowed_missing_dates']
    assert w.step(now=NOW+timedelta(seconds=72),fetch=forbidden,code_commit=NEW)['status']==r.FINISHED
    assert w.audit(now=NOW+timedelta(seconds=72))['ready_for_review']
    assert all(state(f)['completed'][d]==v for d,v in before['completed'].items())
    assert sum(e['weight'] for e in state(f)['events'])==f.post['max_total_reserved_units']
    assert authorize(f,80)[0]['status']=='already_authorized'


@pytest.mark.parametrize('key,value', [
    ('schema',True),('retries',True),('retries',1),('id','other'),('canonical_ledger','/other'),
    ('checkpoint_sha256','0'*64),('parent_pins',['0'*64]*4),('execution_commit','bad'),
    ('allowed_missing_dates',[]),('allowed_missing_dates',['2024-11-25']),
    ('max_additional_attempts',4),('max_total_attempts',999),('max_total_reserved_units',99999),
    ('caps',{'rolling60s':999999}),('scientific_changes',True),('on_failure','retry')])
def test_policy_drift_refuses_before_mutation(settled,key,value):
    f=settled; f.post[key]=value
    with pytest.raises(ValueError): authorize(f)
    assert (f.root/'state.json').read_bytes()==f.checkpoint
    assert not (f.root/r.CHECKPOINT).exists()


def test_out_of_order_or_duplicate_dates_cannot_authorize(settled):
    f=settled
    for dates in [f.post['allowed_missing_dates'][::-1],f.post['allowed_missing_dates']*2]:
        f.post['allowed_missing_dates']=dates
        with pytest.raises(ValueError):authorize(f)


def test_pin_source_unknown_fields_and_all_parents_required(settled):
    f=settled; raw=c.canonical(f.post)
    for pin,code in [('0'*64,NEW),(c.digest(raw),OLD)]:
        with pytest.raises(ValueError):r.authorize_once(f.root,raw,pin,*f.parents,now=NOW+timedelta(seconds=60),code_commit=code)
    for i in range(4):
        parents=list(f.parents);parents[i]=None
        with pytest.raises(ValueError):r.authorize_once(f.root,raw,c.digest(raw),*parents,now=NOW+timedelta(seconds=60),code_commit=NEW)
    f.post['hidden_retry']=True
    with pytest.raises(ValueError):authorize(f)
    assert (f.root/'state.json').read_bytes()==f.checkpoint


@pytest.mark.parametrize('defect',['paused','inflight','missing_success'])
def test_unsettled_or_incomplete_retry_cannot_continue(settled,defect):
    f=settled;s=state(f)
    if defect=='paused':s['paused']='STOP'
    elif defect=='inflight':s['inflight']={'day':f.transport_policy['failed_day'],'source':{'kind':'reserved_http_attempt'}}
    else:del s['completed'][f.transport_policy['failed_day']]
    c.save_state(f.root,s);f.post['checkpoint_sha256']=c.digest((f.root/'state.json').read_bytes())
    with pytest.raises(ValueError):authorize(f)


@pytest.mark.parametrize('defect',['raw','symlink','backup','policy'])
def test_unrecognized_artifacts_refuse(settled,defect,tmp_path):
    f=settled;raw=f.root/'raw'/f'ifs-{f.post["allowed_missing_dates"][0]}.json'
    if defect=='raw':raw.write_bytes(b'good')
    elif defect=='symlink':raw.symlink_to(tmp_path/'missing')
    elif defect=='backup':(f.root/r.CHECKPOINT).write_bytes(b'wrong')
    else:(f.root/r.POLICY).write_bytes(b'wrong')
    with pytest.raises((ValueError,OSError)):authorize(f)
    assert (f.root/'state.json').read_bytes()==f.checkpoint


def test_stale_workers_and_wrong_execution_source_fail_closed(settled):
    f=settled;_,pin=authorize(f)
    for w in [transport.TransportRetryWorker(f.root,*f.parents),retry.CalendarRecoveryWorker(f.root,*f.parents[:3]),
              c.CacheWorker(f.root,recovery_pin=f.rpin,continuation_pin=f.cpin),worker(f,None),worker(f,'0'*64)]:
        with pytest.raises(ValueError):w.step(now=NOW+timedelta(seconds=61),fetch=forbidden,code_commit=NEW)
    with pytest.raises(ValueError):worker(f,pin).step(now=NOW+timedelta(seconds=61),fetch=forbidden,code_commit=OLD)


def test_new_failure_survives_reauthorization_and_never_retries(settled):
    f=settled;_,pin=authorize(f);w=worker(f,pin)
    def fail(*a):raise c.TransportFailure(error_class='URLError',returncode=2)
    assert w.step(now=NOW+timedelta(seconds=60),fetch=fail,code_commit=NEW)['status']=='paused'
    before=(f.root/'state.json').read_bytes()
    assert authorize(f,70)[0]['status']=='already_authorized'
    assert (f.root/'state.json').read_bytes()==before
    assert w.step(now=NOW+timedelta(days=1),fetch=forbidden,code_commit=NEW)['status']=='paused'
    assert len(state(f)['events'])==len(c.parse_json(f.checkpoint)['events'])+1


@pytest.mark.parametrize('defect',['checkpoint','policy','history','old_receipt','identity','authorization','sequence','retry_ordinal','new_receipt','unreserved','orphan'])
def test_live_tamper_refuses_before_transport(settled,defect):
    f=settled;_,pin=authorize(f);w=worker(f,pin)
    assert w.step(now=NOW+timedelta(seconds=60),fetch=lambda *a:b'good',code_commit=NEW)['status']=='downloaded'
    s=state(f)
    if defect=='checkpoint':(f.root/r.CHECKPOINT).write_bytes(b'{}')
    elif defect=='policy':(f.root/r.POLICY).write_bytes(b'{}')
    else:
        if defect=='history':s['events'][0]['weight']+=1
        elif defect=='old_receipt':next(iter(s['completed'].values()))['source']['extra']=True
        elif defect=='identity':s['transport_retry']['enabled_at']=NOW.isoformat()
        elif defect=='authorization':s['calendar_continuation']['policy_sha256']=f.tpin
        elif defect=='sequence':s['events'][-1]['day']=f.post['allowed_missing_dates'][1]
        elif defect=='retry_ordinal':s['events'][-1]['attempt_ordinal']=2
        elif defect=='new_receipt':s['completed'][f.post['allowed_missing_dates'][0]]['source']={'kind':'import'}
        elif defect=='unreserved':s['completed'][f.post['allowed_missing_dates'][1]]=deepcopy(next(iter(s['completed'].values())))
        else:del s['completed'][f.post['allowed_missing_dates'][0]]
        c.save_state(f.root,s)
    with pytest.raises((ValueError,OSError)):w.step(now=NOW+timedelta(seconds=65),fetch=forbidden,code_commit=NEW)


@pytest.mark.parametrize('saved',[False,True])
def test_crash_never_repeats_get_and_only_recovers_pinned_bytes(settled,monkeypatch,saved):
    f=settled;_,pin=authorize(f);w=worker(f,pin);complete=w.complete
    def crash(*a,**kw):raise SystemExit('synthetic crash')
    if saved:monkeypatch.setattr(w,'complete',crash)
    with pytest.raises(SystemExit):w.step(now=NOW+timedelta(seconds=60),fetch=(lambda *a:b'good') if saved else crash,code_commit=NEW)
    monkeypatch.setattr(w,'complete',complete)
    assert w.step(now=NOW+timedelta(seconds=65),fetch=forbidden,code_commit=NEW)['status']==('recovered_without_http' if saved else 'paused')
    if saved:
        with w.locked(NOW+timedelta(seconds=66)):pass
    assert len(state(f)['events'])==len(c.parse_json(f.checkpoint)['events'])+1


def test_authorization_disk_failure_is_offline_and_resumable(settled,monkeypatch):
    f=settled;real=r.save_state
    def fail(*a):raise OSError('synthetic disk failure')
    monkeypatch.setattr(r,'save_state',fail)
    with pytest.raises(OSError):authorize(f)
    assert (f.root/'state.json').read_bytes()==f.checkpoint
    assert (f.root/r.CHECKPOINT).read_bytes()==f.checkpoint
    monkeypatch.setattr(r,'save_state',real)
    assert authorize(f)[0]['network_requests']==0


def test_quota_and_worker_lock_preserve_remaining_attempt(settled,monkeypatch):
    f=settled
    with (f.root/'worker.lock').open('rb') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(ValueError):authorize(f)
    _,pin=authorize(f);w=worker(f,pin);events=state(f)['events']
    monkeypatch.setattr(w,'wait_until',lambda *a:NOW+timedelta(days=1))
    assert w.step(now=NOW+timedelta(seconds=60),fetch=forbidden,code_commit=NEW)['status']=='quota_wait'
    assert state(f)['events']==events


def test_spacing_uses_response_end_and_rollback_refuses(settled):
    f=settled;_,pin=authorize(f);w=worker(f,pin)
    times=iter([NOW+timedelta(seconds=60),NOW+timedelta(seconds=80)])
    assert w.step(now=NOW+timedelta(seconds=60),fetch=lambda *a:b'good',code_commit=NEW,clock=lambda:next(times))['status']=='downloaded'
    assert w.step(now=NOW+timedelta(seconds=81),fetch=forbidden,code_commit=NEW)['status']=='quota_wait'
    with pytest.raises(ValueError,match='rollback'):w.step(now=NOW+timedelta(seconds=79),fetch=forbidden,code_commit=NEW)


def test_controller_routes_all_pins_and_never_auto_retries(settled):
    f=settled;_,pin=authorize(f)
    kwargs=dict(recovery_pin=f.rpin,continuation_pin=f.cpin,calendar_retry_pin=f.calendar_pin,
                transport_retry_pin=f.tpin,post_retry_continuation_pin=pin,code_commit=NEW,
                max_requests=1,max_seconds=60,fetch=lambda *a:b'good',clock=lambda:NOW+timedelta(seconds=60),
                monotonic=lambda:0.0,sleep=forbidden,guard=lambda:None)
    assert q.run_batch(f.root,**kwargs)=={'status':'batch_request_limit','downloaded':1,'automatic_retries':0}
    for key in ('calendar_retry_pin','transport_retry_pin','post_retry_continuation_pin'):
        bad={**kwargs,key:None}
        with pytest.raises(ValueError):q.run_batch(f.root,**bad)


def test_cli_offline_authorization_batch_and_source_guard(settled,monkeypatch,tmp_path,capsys):
    f=settled;cli=module('post_cli',Path(__file__).parents[1]/'scripts/forecast_replay_post_retry.py')
    class Clock:
        @staticmethod
        def now(zone):return NOW+timedelta(seconds=60)
    monkeypatch.setattr(cli,'datetime',Clock);monkeypatch.setattr(cli,'frozen_head',lambda:NEW)
    monkeypatch.setattr(cli,'bounded_fetch',forbidden)
    raw=c.canonical(f.post);pin=c.digest(raw);p=tmp_path/'policy.json';p.write_bytes(raw)
    args=['--cache',str(f.root),'--recovery-sha256',f.rpin,'--continuation-sha256',f.cpin,
          '--calendar-retry-sha256',f.calendar_pin,'--transport-retry-sha256',f.tpin,
          '--post-retry-continuation-sha256',pin,'--confirm-code-commit',NEW]
    assert cli.main(['authorize',*args,'--amendment',str(p)])==0
    assert cli.main(['status',*args])==0
    def run(root,**kw):
        assert root==f.root and kw['max_requests']==1 and kw['max_seconds']==60
        assert kw['post_retry_continuation_pin']==pin and kw['transport_retry_pin']==f.tpin
        kw['guard']();return {'status':'paused','automatic_retries':0}
    monkeypatch.setattr(cli,'run_batch',run)
    assert cli.main(['batch',*args,'--max-requests','1','--max-seconds','60'])==4
    monkeypatch.setattr(cli,'frozen_head',lambda:OLD)
    assert cli.main(['status',*args])==2
