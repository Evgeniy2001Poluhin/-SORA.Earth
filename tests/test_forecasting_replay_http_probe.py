"""Synthetic failed journals/fake GETs only; owner cache and network never opened."""
import base64
from copy import deepcopy
from datetime import timedelta
import fcntl
import importlib.util
from pathlib import Path

import pytest

from app.services.forecasting import replay_cache_worker as c
from app.services.forecasting import replay_http_probe as r
from app.services.forecasting.replay_post_retry_continuation import PostRetryWorker

spec=importlib.util.spec_from_file_location('probe_parent',Path(__file__).with_name('test_forecasting_replay_post_retry.py'))
prior=importlib.util.module_from_spec(spec);spec.loader.exec_module(prior)
settled=prior.settled
NOW=prior.NOW;NEW='1'*40


def forbidden(*a,**kw):pytest.fail('second or unauthorized transport')


@pytest.fixture
def failed(settled):
    f=settled;_,postpin=prior.authorize(f)
    def fail(*a):raise c.TransportFailure(400,error_class='HTTPError',returncode=2)
    assert prior.worker(f,postpin).step(now=NOW+timedelta(seconds=60),fetch=fail,code_commit=prior.NEW)['status']=='paused'
    f.failed=(f.root/'state.json').read_bytes();s=c.parse_json(f.failed)
    f.parents=(*f.parents,postpin)
    f.probe={'schema':1,'id':'M3-P1-ONE-HTTP-PROBE','canonical_ledger':str(f.root.resolve()),
             'failed_state_sha256':c.digest(f.failed),'failed_day':s['inflight']['day'],
             'parent_pins':list(f.parents),'execution_commit':NEW,
             'max_total_attempts':sum(e['kind']=='attempt' for e in s['events'])+1,
             'max_total_reserved_units':sum(e['weight'] for e in s['events'])+21,
             'max_requests':1,'caps':deepcopy(r.CAPS),'scientific_changes':False}
    return f


def call(f,fetch=forbidden,**overrides):
    raw=c.canonical(f.probe);kw={'code_commit':NEW,'fetch':fetch,'clock':lambda:NOW+timedelta(seconds=70),'guard':lambda:None}
    kw.update(overrides)
    return r.probe_once(f.root,raw,c.digest(raw),f.parents,**kw)


def state(f):return c.parse_json((f.root/'state.json').read_bytes())


def test_one_success_stays_diagnostic_only_and_original_failure_preserved(failed):
    f=failed;calls=[];before=c.parse_json(f.failed)
    def fetch(url,timeout,limit):
        s=state(f);assert len(s['events'])==len(before['events'])+1 and s['http_probe']['outcome'] is None
        assert s['events'][-1]['attempt_ordinal']==2 and s['paused']==before['paused']
        calls.append(url);return b'good'
    result=call(f,fetch)
    assert len(calls)==1 and result['status']=='diagnostic_finished_stop' and not result['collection_resumed']
    assert result['outcome']=={'kind':'http_response','http_status':200,'valid_weather_grid':True}
    s=state(f)
    for key in ('completed','paused','inflight','failure_diagnostic','transport_retry','post_retry_continuation'):
        assert s[key]==before[key]
    assert s['events'][:-1]==before['events'] and sum(e['weight'] for e in s['events'])==f.probe['max_total_reserved_units']
    assert (f.root/r.RESPONSE).read_bytes()==b'good' and (f.root/r.CHECKPOINT).read_bytes()==f.failed
    assert not (f.root/'raw'/f'ifs-{f.probe["failed_day"]}.json').exists()
    with pytest.raises(ValueError,match='no repeat'):call(f)
    with pytest.raises(ValueError):
        PostRetryWorker(f.root,*f.parents).step(now=NOW+timedelta(seconds=80),fetch=forbidden,code_commit=prior.NEW)


def test_http400_saves_existing_error_body_without_retry(failed):
    f=failed;body=b'{"reason":"synthetic unavailable run"}'
    def fetch(*a):
        raise c.TransportFailure(400,error_class='HTTPError',returncode=2,http_error_body={
            'base64':base64.b64encode(body).decode(),'bytes':len(body),'sha256':c.digest(body),'truncated':False})
    result=call(f,fetch)
    assert result['outcome']=={'kind':'transport_failure','http_status':400,'error_class':'HTTPError','returncode':2,'body_truncated':False}
    assert (f.root/r.RESPONSE).read_bytes()==body and result['response']['sha256']==c.digest(body)
    assert state(f)['failure_diagnostic']==c.parse_json(f.failed)['failure_diagnostic']
    with pytest.raises(ValueError):call(f)


@pytest.mark.parametrize('key,value',[
    ('schema',True),('id','other'),('max_requests',2),('max_requests',True),('canonical_ledger','/other'),
    ('failed_state_sha256','0'*64),('failed_day','2024-01-01'),('parent_pins',['0'*64]*5),
    ('execution_commit','0'*40),('max_total_attempts',999),('max_total_reserved_units',99999),
    ('caps',{'rolling86400s':999999}),('scientific_changes',True)])
def test_policy_drift_refuses_before_any_mutation_or_get(failed,key,value):
    f=failed;f.probe[key]=value
    with pytest.raises(ValueError):call(f)
    assert (f.root/'state.json').read_bytes()==f.failed
    assert not (f.root/r.CHECKPOINT).exists()


def test_wrong_external_pin_missing_parent_and_unknown_field_refuse(failed):
    f=failed;raw=c.canonical(f.probe)
    with pytest.raises(ValueError):r.probe_once(f.root,raw,'0'*64,f.parents,code_commit=NEW,fetch=forbidden,clock=lambda:NOW,guard=forbidden)
    for i in range(5):
        parents=list(f.parents);parents[i]=None
        with pytest.raises(ValueError):r.probe_once(f.root,raw,c.digest(raw),parents,code_commit=NEW,fetch=forbidden,clock=lambda:NOW,guard=forbidden)
    f.probe['extra_retry']=True
    with pytest.raises(ValueError):call(f)
    assert (f.root/'state.json').read_bytes()==f.failed


@pytest.mark.parametrize('lock',['worker.lock','controller.lock'])
def test_shared_locks_block_probe_before_mutation(failed,lock):
    f=failed
    with (f.root/lock).open('a+b') as h:
        fcntl.flock(h,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises((ValueError,BlockingIOError)):call(f)
    assert (f.root/'state.json').read_bytes()==f.failed


def test_quota_wait_does_not_reserve_or_mutate(failed,monkeypatch):
    f=failed;monkeypatch.setattr(c.CacheWorker,'wait_until',lambda *a:NOW+timedelta(days=1))
    assert call(f)=={'status':'quota_wait','not_before':(NOW+timedelta(days=1)).isoformat(),'network_requests':0}
    assert (f.root/'state.json').read_bytes()==f.failed and not (f.root/r.POLICY).exists()


def test_crash_after_reservation_never_reissues_get(failed):
    f=failed
    def crash(*a):raise SystemExit('synthetic crash')
    with pytest.raises(SystemExit):call(f,crash)
    assert state(f)['http_probe']['outcome'] is None
    with pytest.raises(ValueError,match='no repeat'):call(f)


def test_guard_changed_after_reservation_consumes_permission_without_get(failed):
    f=failed;calls=[]
    def guard():
        calls.append(True)
        if len(calls)==2:raise ValueError('source changed')
    assert call(f,guard=guard)['outcome']=={'kind':'uncertain_failure_stop'}
    with pytest.raises(ValueError):call(f)


def test_disk_failure_after_get_never_repeats(failed,monkeypatch):
    f=failed;calls=[]
    def fetch(*a):calls.append(True);return b'good'
    def fail(*a):raise OSError('synthetic output failure')
    monkeypatch.setattr(r,'write_new',fail)
    with pytest.raises(OSError):call(f,fetch)
    assert len(calls)==1 and state(f)['http_probe']['response']['sha256']==c.digest(b'good')
    with pytest.raises(ValueError):call(f)


def test_completion_time_and_no_body_failure_remain_charged(failed):
    f=failed;times=iter([NOW+timedelta(seconds=70),NOW+timedelta(seconds=70),NOW+timedelta(seconds=90)])
    def fetch(*a):raise c.TransportFailure(error_class='URLError',returncode=2)
    result=call(f,fetch,clock=lambda:next(times))
    assert result['response'] is None and not (f.root/r.RESPONSE).exists()
    assert state(f)['events'][-1]['at']==(NOW+timedelta(seconds=90)).isoformat()
    with pytest.raises(ValueError):call(f)


@pytest.mark.parametrize('defect',['checkpoint','policy','response','weather_raw'])
def test_existing_artifact_collision_refuses(failed,defect):
    f=failed
    path={'checkpoint':f.root/r.CHECKPOINT,'policy':f.root/r.POLICY,'response':f.root/r.RESPONSE,
          'weather_raw':f.root/'raw'/f'ifs-{f.probe["failed_day"]}.json'}[defect]
    path.write_bytes(b'collision')
    with pytest.raises(ValueError):call(f)
    assert (f.root/'state.json').read_bytes()==f.failed


def test_unexpected_weather_parser_failure_keeps_received_bytes(failed,monkeypatch):
    f=failed;calls=[]
    def validate(*a):raise OverflowError('synthetic parser overflow')
    def fetch(*a):calls.append(True);return b'good'
    monkeypatch.setattr(c.CacheWorker,'validate',validate)
    result=call(f,fetch)
    assert result['outcome']=={'kind':'http_response','http_status':200,'valid_weather_grid':False}
    assert result['response']['sha256']==c.digest(b'good') and (f.root/r.RESPONSE).read_bytes()==b'good'
    assert len(calls)==1 and not state(f)['completed'].keys()-c.parse_json(f.failed)['completed'].keys()
    with pytest.raises(ValueError):call(f)
