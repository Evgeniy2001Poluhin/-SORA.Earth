"""Tmp paths, synthetic plan, fake transport/clock; no public requests."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import fcntl
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.forecasting import replay_cache_worker as c
from app.services.forecasting import replay_cache_recovery as r
from app.services.forecasting.replay_cache_plan import _request

NOW = datetime(2026, 10, 22, 16, tzinfo=timezone.utc)
OLD, NEW = 'a' * 40, 'b' * 40


@pytest.fixture
def failed(tmp_path, monkeypatch):
    plan = c.build_plan()
    dates = [date(2024, 11, 25) + timedelta(days=i) for i in range(3)]
    plan['requests'] = [_request(d) for d in dates]
    plan['probe_requests'], plan['reuse_candidates'] = [], []
    monkeypatch.setattr(c, 'build_plan', lambda: plan)
    monkeypatch.setattr(r, 'stage_dates', lambda: {str(d) for d in dates})
    root = tmp_path / 'cache';raw = c.canonical(plan)
    c.initialize(root, raw, c.digest(raw), prior_units=0, now=NOW, code_commit=OLD)
    w = c.CacheWorker(root)
    def fail(*a): raise c.TransportFailure(error_class='URLError', returncode=2)
    assert w.step(now=NOW, fetch=fail, code_commit=OLD)['status'] == 'paused'
    original = (root / 'state.json').read_bytes()
    p = {'schema':1, 'id':'M3-P1-ONE-RECOVERY-2026-10-22', 'retry_limit':1,
         'failed_day':'2024-11-25', 'canonical_ledger':str(root.resolve()),
         'failed_state_sha256':c.digest(original), 'execution_commit':NEW,
         'caps':{'rolling60s':400,'rolling3600s':4000,'rolling86400s':7000,'spacing_seconds_after_response':4},
         'scientific_changes':False, 'on_failure':'stop_no_more_retries',
         'allowed_missing_dates':[str(d) for d in dates], 'original_attempts':1,
         'max_total_attempts':4, 'max_total_reserved_units':84}
    return SimpleNamespace(root=root, p=p, original=original)


def authorize(f, **kw):
    raw=c.canonical(f.p);pin=c.digest(raw)
    result=r.authorize_once(f.root, raw, pin, now=NOW+timedelta(seconds=10), code_commit=kw.get('code', NEW))
    return result,pin


def worker(f,pin):
    w=c.CacheWorker(f.root,recovery_pin=pin)
    w.validate=lambda raw,req:c.need(raw==b'good','invalid synthetic raw')
    return w


def forbidden(*a):pytest.fail('unexpected HTTP/retry')


def state(f):return c.parse_json((f.root/'state.json').read_bytes())


def test_authorization_preserves_failure_bytes_events_and_charge(failed):
    f=failed;result,pin=authorize(f)
    assert result['network_requests']==0 and result['failed_attempt_preserved']
    assert (f.root/r.BACKUP).read_bytes()==f.original
    old=c.parse_json(f.original);current=state(f)
    assert current['events']==old['events'] and current['completed']==old['completed']
    assert current['code_commit']==OLD and current['paused'] is None and current['inflight'] is None
    assert current['manual_recovery']['amendment_sha256']==pin


def test_exactly_one_repeat_reserves_before_fetch_then_only_remaining_dates(failed):
    f=failed;_,pin=authorize(f);w=worker(f,pin);calls=[]
    def fetch(url,*a):
        s=state(f);assert len(s['events'])==len(calls)+3
        if not calls:
            assert s['events'][-1]['attempt_ordinal']==2
            assert s['events'][-1]['recovery_amendment_sha256']==pin
            assert sum(e['weight'] for e in s['events'])==42
        calls.append(url);return b'good'
    for i in range(3):
        out=w.step(now=NOW+timedelta(seconds=10+4*i),fetch=fetch,code_commit=NEW)
        assert out['status']=='downloaded'
    assert w.step(now=NOW+timedelta(seconds=22),fetch=forbidden,code_commit=NEW)['status']=='first_slice_complete'
    assert len(calls)==3 and len(state(f)['events'])==5
    assert sum(e['weight'] for e in state(f)['events'])==84
    assert state(f)['events'][:2]==c.parse_json(f.original)['events']


@pytest.mark.parametrize('field,value',[
    ('retry_limit',2),('retry_limit',True),('schema',True),('original_attempts',True),('failed_day','2024-11-26'),('canonical_ledger','/other'),
    ('failed_state_sha256','0'*64),('execution_commit','bad'),('scientific_changes',True),
    ('on_failure','retry'),('allowed_missing_dates',['2024-11-25']),
    ('original_attempts',0),('max_total_attempts',10),('max_total_reserved_units',999),
    ('caps',{'rolling60s':999})])
def test_bad_amendment_refused_without_changing_ledger(failed,field,value):
    f=failed;f.p[field]=value
    with pytest.raises(ValueError):authorize(f)
    assert (f.root/'state.json').read_bytes()==f.original


def test_wrong_external_pin_or_code_refuses(failed):
    f=failed;raw=c.canonical(f.p)
    with pytest.raises(ValueError):r.authorize_once(f.root,raw,'0'*64,now=NOW+timedelta(seconds=10),code_commit=NEW)
    with pytest.raises(ValueError):authorize(f,code=OLD)
    assert (f.root/'state.json').read_bytes()==f.original


def test_normal_worker_without_explicit_pin_cannot_use_amendment(failed):
    f=failed;_,pin=authorize(f)
    with pytest.raises(ValueError,match='explicit recovery'):c.CacheWorker(f.root).step(now=NOW+timedelta(seconds=11),fetch=forbidden,code_commit=NEW)
    with pytest.raises(ValueError,match='commit'):worker(f,pin).step(now=NOW+timedelta(seconds=11),fetch=forbidden,code_commit=OLD)


def test_second_failure_stays_paused_even_after_reauthorization(failed):
    f=failed;_,pin=authorize(f);w=worker(f,pin)
    def fail(*a):raise c.TransportFailure(429,error_class='HTTPError',returncode=2)
    assert w.step(now=NOW+timedelta(seconds=10),fetch=fail,code_commit=NEW)['status']=='paused'
    before=(f.root/'state.json').read_bytes()
    out,_=authorize(f);assert out['status']=='already_authorized' and out['paused']
    assert (f.root/'state.json').read_bytes()==before
    assert w.step(now=NOW+timedelta(seconds=70),fetch=forbidden,code_commit=NEW)['status']=='paused'
    assert len(state(f)['events'])==3


def test_crash_without_raw_never_repeats_controlled_attempt(failed):
    f=failed;_,pin=authorize(f);w=worker(f,pin)
    def crash(*a):raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):w.step(now=NOW+timedelta(seconds=10),fetch=crash,code_commit=NEW)
    assert w.step(now=NOW+timedelta(seconds=20),fetch=forbidden,code_commit=NEW)['status']=='paused'
    assert len(state(f)['events'])==3


def test_complete_pinned_raw_recovers_locally_without_http(failed,monkeypatch):
    f=failed;_,pin=authorize(f);w=worker(f,pin);real=w.complete
    monkeypatch.setattr(w,'complete',lambda *a,**kw:(_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):w.step(now=NOW+timedelta(seconds=10),fetch=lambda *a:b'good',code_commit=NEW)
    monkeypatch.setattr(w,'complete',real)
    assert w.step(now=NOW+timedelta(seconds=20),fetch=forbidden,code_commit=NEW)['status']=='recovered_without_http'
    assert len(state(f)['events'])==3


def test_quota_and_spacing_are_still_enforced(failed):
    f=failed;_,pin=authorize(f);w=worker(f,pin)
    s=state(f)
    # New synthetic traffic may not be injected into an authorized history.
    s['events'].append({'kind':'attempt','day':'2024-11-27','weight':21,'at':NOW.isoformat()})
    c.save_state(f.root,s)
    with pytest.raises(ValueError):w.step(now=NOW+timedelta(seconds=11),fetch=forbidden,code_commit=NEW)


def test_original_backup_and_events_cannot_be_rewritten(failed):
    f=failed;_,pin=authorize(f);w=worker(f,pin)
    s=state(f);s['events'][0]['weight']=0;s['events'][1]['weight']=0;c.save_state(f.root,s)
    with pytest.raises(ValueError):w.step(now=NOW+timedelta(seconds=11),fetch=forbidden,code_commit=NEW)


def test_lock_and_existing_raw_refuse(failed):
    f=failed;raw=c.canonical(f.p)
    with (f.root/'worker.lock').open('rb') as fd:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(ValueError):authorize(f)
    (f.root/'raw/ifs-2024-11-25.json').write_bytes(b'existing')
    with pytest.raises(ValueError):authorize(f)
    assert (f.root/'state.json').read_bytes()==f.original


def test_crash_in_offline_authorization_is_resumable_without_reset(failed,monkeypatch):
    f=failed;save=r.save_state
    monkeypatch.setattr(r,'save_state',lambda *a:(_ for _ in ()).throw(OSError('injected')))
    with pytest.raises(OSError):authorize(f)
    assert (f.root/'state.json').read_bytes()==f.original and (f.root/r.BACKUP).read_bytes()==f.original
    monkeypatch.setattr(r,'save_state',save)
    assert authorize(f)[0]['status']=='authorized_once'


@pytest.mark.parametrize('weight,seconds', [(400,60), (4000,3600), (7000,86400)])
def test_amended_worker_cannot_spend_seeded_rolling_cap(failed,weight,seconds):
    f=failed
    # Mechanical quota fixture: re-pin synthetic original before authorization.
    original=c.parse_json(f.original);original['events'][0]['weight']=weight
    f.original=c.canonical(original);(f.root/'state.json').write_bytes(f.original)
    f.p['failed_state_sha256']=c.digest(f.original)
    f.p['max_total_reserved_units']=weight+84
    _,pin=authorize(f);w=worker(f,pin)
    result=w.step(now=NOW+timedelta(seconds=10),fetch=forbidden,code_commit=NEW)
    assert result['status']=='quota_wait'
    assert result['not_before']==(NOW+timedelta(seconds=seconds)).isoformat()
    assert len(state(f)['events'])==2


def test_amended_worker_keeps_completion_spacing(failed):
    f=failed;_,pin=authorize(f);w=worker(f,pin)
    assert w.step(now=NOW+timedelta(seconds=10),fetch=lambda *a:b'good',code_commit=NEW)['status']=='downloaded'
    result=w.step(now=NOW+timedelta(seconds=13),fetch=forbidden,code_commit=NEW)
    assert result=={'status':'quota_wait','not_before':(NOW+timedelta(seconds=14)).isoformat()}
