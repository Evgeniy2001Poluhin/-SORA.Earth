"""Temporary synthetic parent ledgers, injected transport; no real cache or GET."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import base64
import fcntl
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.services.forecasting import replay_gap_collector as m
from app.services.forecasting import replay_cache_worker as c
from app.services.forecasting.replay_source_gap_contract import build_contract, canonical, digest

spec=importlib.util.spec_from_file_location('gap_contract_fixture',Path(__file__).with_name('test_forecasting_replay_source_gap_contract.py'))
prior=importlib.util.module_from_spec(spec);spec.loader.exec_module(prior)
NOW=datetime(2026,10,24,12,tzinfo=timezone.utc)
OLD=NOW-timedelta(days=2)
CODE='7'*40
ORIGINAL_VALIDATE=c.CacheWorker.validate


def forbidden(*args):
    pytest.fail('unreviewed or repeated transport')


def repin(f):
    state_raw=canonical(f.state);(f.parent/'state.json').write_bytes(state_raw)
    f.inv['state_sha256']=digest(state_raw)
    f.bundle={'science.json':canonical(f.science),'inventory.json':canonical(f.inv),'authorization.txt':b'synthetic owner scope'}
    contract=build_contract(f.bundle['science.json'],f.bundle['inventory.json'],state_raw,
        parent_pin=digest(f.bundle['science.json']),inventory_pin=digest(f.bundle['inventory.json']),
        state_pin=digest(state_raw),authorization_pin=digest(f.bundle['authorization.txt']))
    f.bundle['contract.json']=canonical(contract);f.contract=contract
    f.policy={'schema':1,'id':'M3-P1-COMPANION112','parent_root':str(f.parent),'root':str(f.root),
        'execution_commit':CODE,'parent_state_sha256':digest(state_raw),'parent_plan_sha256':f.state['plan_sha256'],
        'bundle_pins':{k:digest(v) for k,v in f.bundle.items()},'parent_policy_pins':f.parents}
    f.policy_raw=canonical(f.policy)
    f.worker=m.CompanionCollector(f.parent,f.root,f.policy_raw,digest(f.policy_raw),code_commit=CODE)


@pytest.fixture
def prepared(tmp_path,monkeypatch):
    science,inv,state=prior.inputs.__wrapped__()
    parent=tmp_path/'parent';parent.mkdir();(parent/'raw').mkdir()
    for lock in ['worker.lock','controller.lock']:(parent/lock).write_bytes(b'')
    plan=c.build_plan();plan_raw=c.canonical(plan);(parent/'plan.json').write_bytes(plan_raw)
    science['parent_plan_sha256']=state['plan_sha256']=inv['plan_sha256']=digest(plan_raw)
    days=[r['day'] for r in inv['rows']];gap='2025-08-05'
    complete={r['issue_date'] for r in plan['reuse_candidates']+plan['probe_requests']}
    for day in days:
        if len(complete)==494:break
        if day!=gap:complete.add(day)
    receipt={'sha256':digest(b'parent-synthetic'),'bytes':len(b'parent-synthetic')}
    state['completed']={d:deepcopy(receipt) for d in sorted(complete)}
    inv['rows']=[dict(day=d,status='provider_unavailable_observed' if d==gap else
        'receipted_hash_checked' if d in complete else 'not_attempted',**(receipt if d in complete else {})) for d in days]
    for d in complete:(parent/'raw'/('ifs-'+d+'.json')).write_bytes(b'parent-synthetic')
    state['events']=([{'kind':'prior_allowance','weight':2472,'at':OLD.isoformat()}]+
        [{'kind':'attempt','day':d,'weight':21,'at':OLD.isoformat()} for d in sorted(complete)[:484]]+
        [{'kind':'attempt','day':gap,'weight':21,'at':OLD.isoformat()}])
    state['last_seen']=OLD.isoformat()
    parents={}
    for name in m.PARENTS:
        raw=canonical({'id':name});(parent/name).write_bytes(raw);parents[name]=digest(raw)
    state['http_probe']['policy_sha256']=inv['probe_evidence']['policy_sha256']=parents['http-probe-policy.json']
    body=canonical({'reason':'synthetic recorded response','error':True});(parent/'http-probe-response.bin').write_bytes(body)
    state['http_probe']['response'].update(file='http-probe-response.bin',bytes=len(body),sha256=digest(body))
    inv['probe_evidence']['response_sha256']=digest(body)
    f=SimpleNamespace(parent=parent,root=tmp_path/'companion',state=state,inv=inv,science=science,parents=parents)
    repin(f)
    monkeypatch.setattr(c.CacheWorker,'validate',lambda self,raw,request:c.need(raw==b'good','synthetic grid failure'))
    return f


def init(f,guard=lambda:None):return f.worker.initialize(f.bundle,now=NOW,guard=guard)
def state(f):return json.loads((f.root/'state.json').read_bytes())
def step(f,fetch=lambda *args:b'good',now=NOW,guard=lambda:None,clock=None):
    return f.worker.step(fetch=fetch,clock=clock or (lambda:now),guard=guard)


def test_one_reservation_before_transport_preserves_parent_and_skips_receipt(prepared):
    f=prepared;before={str(p.relative_to(f.parent)):p.read_bytes() for p in f.parent.rglob('*') if p.is_file()}
    assert init(f)['network_requests']==0
    calls=[]
    def fetch(url,*args):
        s=state(f);assert len(s['events'])==1 and s['inflight']=={'day':f.contract['acquisition']['allowed_dates'][0]}
        calls.append(url);return b'good'
    assert step(f,fetch)['status']=='received'
    assert step(f,forbidden,now=NOW+timedelta(seconds=3))['status']=='quota_wait'
    assert step(f,now=NOW+timedelta(seconds=4))['status']=='received'
    assert len(state(f)['completed'])==2 and len(calls)==1
    assert before=={str(p.relative_to(f.parent)):p.read_bytes() for p in f.parent.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):init(f)


@pytest.mark.parametrize('count,age,seconds',[(19,0,60),(190,61,3600),(333,3601,86400)])
def test_rolling_limits_include_parent_history(prepared,count,age,seconds):
    f=prepared
    for e in f.state['events'][-count:]:e['at']=(NOW-timedelta(seconds=age)).isoformat()
    repin(f);init(f);before=(f.root/'state.json').read_bytes()
    result=step(f,forbidden);assert result['status']=='quota_wait'
    assert datetime.fromisoformat(result['not_before'])>=NOW+timedelta(seconds=seconds-age)
    assert (f.root/'state.json').read_bytes()==before


def test_midnight_does_not_reset_day_budget(prepared):
    f=prepared
    for e in f.state['events'][-333:]:e['at']=NOW.isoformat()
    repin(f);init(f)
    assert step(f,forbidden,now=NOW.replace(hour=0)+timedelta(days=1))['status']=='quota_wait'


@pytest.mark.parametrize('lock',['controller.lock','worker.lock'])
def test_parent_locks_exclude_initialization_and_requests(prepared,lock):
    f=prepared
    with (f.parent/lock).open('r+b') as h:
        fcntl.flock(h,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):init(f)
    init(f)
    with (f.parent/lock).open('r+b') as h:
        fcntl.flock(h,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):step(f,forbidden)


@pytest.mark.parametrize('failure',['http','timeout','invalid_grid','crash','guard','disk'])
def test_failure_consumes_one_attempt_and_never_retries(prepared,monkeypatch,failure):
    f=prepared;init(f);calls=[];guards=[]
    def fetch(*args):
        calls.append(True)
        if failure=='http':raise c.TransportFailure(400,error_class='HTTPError',returncode=2)
        if failure=='timeout':raise TimeoutError()
        if failure=='crash':raise SystemExit()
        return b'bad' if failure=='invalid_grid' else b'good'
    def guard():
        guards.append(True)
        if failure=='guard' and len(guards)==2:raise ValueError('source drift')
    if failure=='disk':
        original=m.write_new
        def write(path,raw):
            if path.parent==f.root/'raw':raise OSError('synthetic disk failure')
            return original(path,raw)
        monkeypatch.setattr(m,'write_new',write)
    if failure=='crash':
        with pytest.raises(SystemExit):step(f,fetch,guard=guard)
    else:assert step(f,fetch,guard=guard)['status']=='paused'
    assert len(state(f)['events'])==1
    assert step(f,forbidden,now=NOW+timedelta(minutes=1))['status']=='paused'
    assert len(calls)==(0 if failure=='guard' else 1)


def test_clock_rollback_after_response_keeps_bytes_and_stops(prepared):
    f=prepared;init(f);times=iter([NOW,NOW,NOW-timedelta(seconds=1)])
    assert step(f,clock=lambda:next(times))['status']=='paused'
    assert len(list((f.root/'raw').iterdir()))==1
    assert step(f,forbidden)['status']=='paused'


def test_clock_during_preflight_refuses_before_reservation(prepared):
    f=prepared;init(f);times=iter([NOW,NOW-timedelta(seconds=1)])
    with pytest.raises(ValueError):step(f,forbidden,clock=lambda:next(times))
    assert state(f)['events']==[]


@pytest.mark.parametrize('defect',['parent_raw','parent_state','parent_plan','parent_policy','bundle',
                                'extra_raw','raw_symlink','policy','state_prefix','state_weight'])
def test_corruption_and_symlinks_refuse_no_transport(prepared,defect):
    f=prepared;init(f)
    if defect=='parent_raw':next((f.parent/'raw').iterdir()).write_bytes(b'wrong')
    elif defect=='parent_state':(f.parent/'state.json').write_bytes(b'{}')
    elif defect=='parent_plan':(f.parent/'plan.json').write_bytes(b'{}')
    elif defect=='parent_policy':(f.parent/'manual-recovery.json').write_bytes(b'{}')
    elif defect=='bundle':(f.root/'contract.json').write_bytes(b'{}')
    elif defect=='extra_raw':(f.root/'raw'/'extra').write_bytes(b'x')
    elif defect=='raw_symlink':
        (f.root/'raw').rmdir();(f.root/'raw').symlink_to(f.parent/'raw',target_is_directory=True)
    elif defect=='policy':(f.root/'execution-policy.json').write_bytes(b'{}')
    else:
        step(f);s=state(f)
        if defect=='state_prefix':s['events'][0]['day']='2025-08-05'
        else:s['events'][0]['weight']=0
        c.save_state(f.root,s)
    with pytest.raises((ValueError,OSError)):step(f,forbidden,now=NOW+timedelta(seconds=10))


def test_all112_success_is_only_pending_independent_audit(prepared):
    f=prepared;init(f)
    for n,day in enumerate(f.contract['acquisition']['allowed_dates']):
        result=step(f,now=NOW+timedelta(seconds=4*n));assert result['day']==day
    assert step(f,forbidden,now=NOW+timedelta(seconds=448))['status']=='all112_received_pending606_audit'
    assert len(state(f)['completed'])==112 and len(state(f)['events'])==112


def test_source_guard_before_initialize_and_reservation(prepared):
    f=prepared
    def guard():raise ValueError('not clean source')
    with pytest.raises(ValueError):init(f,guard)
    assert not f.root.exists()
    init(f);before=(f.root/'state.json').read_bytes()
    with pytest.raises(ValueError):step(f,forbidden,guard=guard)
    assert (f.root/'state.json').read_bytes()==before


@pytest.mark.parametrize('defect',[None,'missing_point','missing_hour','wrong_unit'])
def test_original_weather_validator_integration(prepared,monkeypatch,defect):
    f=prepared;init(f)
    monkeypatch.setattr(c.CacheWorker,'validate',ORIGINAL_VALIDATE)
    request=next(r for r in c.build_plan()['requests'] if r['issue_date']==f.contract['acquisition']['allowed_dates'][0])
    p0spec=importlib.util.spec_from_file_location('p0_synthetic_payload',Path(__file__).with_name('test_forecasting_replay_p0.py'))
    fixture=importlib.util.module_from_spec(p0spec);p0spec.loader.exec_module(fixture)
    from app.services.environmental.regions import OPENMETEO_POINTS_V1
    protocol={'points':[{'code':p.code,'lat':p.lat,'lon':p.lon} for p in OPENMETEO_POINTS_V1],
              'forecast_days':8,'forecast_min_hours':24}
    payload=fixture.payload(protocol,{'kind':'forecast','origin':request['origin']})
    if defect=='missing_point':payload.pop()
    elif defect=='missing_hour':
        payload[0]['hourly']['time'].pop();payload[0]['hourly']['temperature_2m'].pop()
    elif defect=='wrong_unit':payload[0]['hourly_units']['temperature_2m']='F'
    raw=c.canonical(payload);assert len(raw)<=c.MAX_RAW
    result=step(f,fetch=lambda *args:raw)
    assert result['status']==('received' if defect is None else 'paused')
    assert len(state(f)['completed'])==(1 if defect is None else 0)
    assert len(state(f)['events'])==1 and state(f)['events'][0]['weight']==21
    if defect is not None:
        assert state(f)['inflight']['day']==request['issue_date']
        assert step(f,forbidden,now=NOW+timedelta(seconds=10))['status']=='paused'


def test_cli_wrong_commit_refuses_before_reading_inputs(tmp_path):
    script=Path(__file__).resolve().parents[1]/'scripts/forecast_replay_gap_collect.py'
    r=subprocess.run([sys.executable,'-B',str(script),'--parent',str(tmp_path/'missing-parent'),
        '--root',str(tmp_path/'new'),'--policy',str(tmp_path/'missing-policy'),
        '--policy-sha256','0'*64,'--execution-commit','0'*40],capture_output=True)
    assert r.returncode==2 and json.loads(r.stderr)=={'status':'refused','error_class':'CacheRefusal'}
    assert not (tmp_path/'new').exists()


@pytest.mark.parametrize('diagnostic_disk_failure',[False,True])
def test_http_diagnostics_pause_is_durable_before_prefix_write(prepared,monkeypatch,diagnostic_disk_failure):
    f=prepared;init(f);body=b'{"reason":"synthetic unavailable","error":true}'
    def fetch(*args):
        raise c.TransportFailure(400,error_class='HTTPError',returncode=2,http_error_body={
            'base64':base64.b64encode(body).decode(),'bytes':len(body),'sha256':digest(body),'truncated':False})
    original=m.write_new
    def write(path,raw):
        if path.parent==f.root/'failures':
            assert state(f)['paused']=='request_failed:TransportFailure:400'
            if diagnostic_disk_failure:raise OSError('diagnostic write failed')
        return original(path,raw)
    monkeypatch.setattr(m,'write_new',write)
    if diagnostic_disk_failure:
        with pytest.raises(OSError):step(f,fetch)
    else:
        assert step(f,fetch)['status']=='paused'
        day=state(f)['inflight']['day']
        meta=json.loads((f.root/'failures'/(day+'.http-error.json')).read_bytes())
        assert meta=={'http_status':400,'error_class':'HTTPError','returncode':2,
                     'prefix':{'bytes':len(body),'sha256':digest(body),'truncated':False}}
        assert (f.root/'failures'/(day+'.http-error-prefix.bin')).read_bytes()==body
    assert len(state(f)['events'])==1 and step(f,forbidden)['status']=='paused'
