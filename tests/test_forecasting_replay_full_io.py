"""Tiny IO doubles exercise pin/atomicity contracts; never read real data."""
from contextlib import contextmanager
from datetime import date
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

from app.services.forecasting import replay_full as f

_spec=importlib.util.spec_from_file_location('full_protocol_fixture',Path(__file__).with_name('test_forecasting_replay_full.py'))
_fixtures=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_fixtures)


@pytest.fixture
def tiny(tmp_path,monkeypatch):
    issue=date(2024,4,1)
    monkeypatch.setattr(f,'ISSUES',(issue,));monkeypatch.setattr(f,'ORIGINS',f.ORIGINS[:2])
    p=_fixtures.protocol()
    cache=tmp_path/'cache';(cache/'raw').mkdir(parents=True)
    p0=tmp_path/'p0';p0.mkdir();out=tmp_path/'snapshot';raw=b'tiny synthetic payload'
    name=f'ifs-{issue}.json';(cache/'raw'/name).write_bytes(raw)
    policy=f.canonical({'execution_commit':p['acquisition_execution_commit']})
    (cache/'calendar-continuation.json').write_bytes(policy);p['continuation_sha256']=f.digest(policy)
    (cache/'state.json').write_bytes(b'{}')
    state={'paused':None,'inflight':None,'code_commit':p['worker_commit'],
           'completed':{str(issue):{'sha256':f.digest(raw),'bytes':len(raw),'source':{'kind':'import'}}}}
    original=f.canonical({});path=tmp_path/'p0protocol.json';path.write_bytes(original)
    monkeypatch.setattr(f,'P0_PROTOCOL_PIN',f.digest(original));monkeypatch.setattr(f,'SEMANTIC_P0',f.digest(original))
    manifest={'entries':[]}
    for name,kind,_ in f.expected_members()[1:]:
        (p0/name).write_bytes(raw);manifest['entries'].append({'file':name,'bytes':len(raw),'sha256':f.digest(raw)})
    monkeypatch.setattr(f,'read_snapshot',lambda *a:(manifest,None,None))
    class Worker:
        def __init__(self,*a,**kw):pass
        @contextmanager
        def locked(self,now):yield state,None,{str(issue):{}}
        def validate(self,*a):pass
    monkeypatch.setattr(f,'CacheWorker',Worker)
    monkeypatch.setattr(f,'parse_payload',lambda raw,p,item:{'kind':item['kind']})
    p['canonical_ledger']=str(cache.resolve())
    return SimpleNamespace(cache=cache,p0=p0,p0protocol=path,p=p,out=out,state=state)


def export(t):return f.export_snapshot(t.cache,t.p0,t.p0protocol,t.p,t.out)


def test_export_load_and_exact_member_order(tiny):
    t=tiny;pin=export(t)
    verification,forecasts,tests=f.load_snapshot(t.out,t.p,pin)
    assert verification=={'kind':'verification'} and set(forecasts)==set(f.ISSUES) and set(tests)==set(f.ORIGINS)
    manifest=f.parse_json((t.out/'manifest.json').read_bytes())
    assert manifest['complete_full_calendar'] is True and len(manifest['entries'])==4
    with pytest.raises(ValueError,match='new full export'):export(t)


@pytest.mark.parametrize('defect',['raw','entry_order','semantic','member_traversal'])
def test_load_refuses_tampered_bytes_or_schema(tiny,defect):
    t=tiny;pin=export(t);path=t.out/'manifest.json';m=f.parse_json(path.read_bytes())
    if defect=='raw':(t.out/m['entries'][0]['file']).write_bytes(b'changed')
    elif defect=='entry_order':m['entries'].reverse()
    elif defect=='semantic':m['p0_protocol']={'changed':True}
    else:m['entries'][0]['file']='../outside.json'
    if defect!='raw':raw=f.canonical(m);path.write_bytes(raw);pin=f.digest(raw)
    with pytest.raises(ValueError):f.load_snapshot(t.out,t.p,pin)


@pytest.mark.parametrize('moment',['before_rename','after_rename'])
def test_export_failure_keeps_partial_or_published_artifact(tiny,monkeypatch,moment):
    t=tiny
    def fail(*a):raise OSError('synthetic fsync error')
    if moment=='before_rename':monkeypatch.setattr(f,'write_new',fail)
    else:monkeypatch.setattr(f,'sync_dir',fail)
    with pytest.raises(OSError):export(t)
    assert t.out.is_dir() == (moment=='after_rename')
    assert t.out.with_name(t.out.name+'.partial').is_dir() == (moment=='before_rename')


@pytest.mark.parametrize('reason',['missing','paused','inflight'])
def test_export_requires_settled_full_cache(tiny,reason):
    t=tiny
    if reason=='missing':t.state['completed'].clear()
    else:t.state[reason]='synthetic'
    with pytest.raises(ValueError,match='not settled'):export(t)


@pytest.fixture
def cli(monkeypatch):
    spec=importlib.util.spec_from_file_location('full_cli_test',Path(__file__).parents[1]/'scripts/forecast_replay_full.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'frozen_head',lambda:'c'*40)
    monkeypatch.setattr(module,'bounded_fetch',lambda *a:pytest.fail('unexpected network'))
    return module


def cli_args(t):
    path=t.out.parent/'protocol.json';raw=f.canonical(t.p);path.write_bytes(raw)
    return ['export','--protocol',str(path),'--protocol-sha256',f.digest(raw),'--output',str(t.out),
            '--cache',str(t.cache),'--p0',str(t.p0),'--p0-protocol',str(t.p0protocol)]


def test_cli_export_is_explicit_and_source_pinned(tiny,cli,capsys):
    args=cli_args(tiny)
    assert cli.main(args)==0
    assert f.parse_json(capsys.readouterr().out.encode())['full_cache_complete'] is True


def test_cli_wrong_pin_refuses_before_export(tiny,cli,monkeypatch):
    args=cli_args(tiny);args[args.index('--protocol-sha256')+1]='0'*64
    monkeypatch.setattr(cli,'export_snapshot',lambda *a:pytest.fail('export despite wrongpin'))
    assert cli.main(args)==2


def test_cli_dirty_source_refuses_before_io(tiny,cli,monkeypatch):
    args=cli_args(tiny)
    monkeypatch.setattr(cli,'frozen_head',lambda:(_ for _ in ()).throw(ValueError()))
    assert cli.main(args)==2 and not tiny.out.exists()


def test_cli_existing_report_refuses_before_reading_quality_data(tiny,cli,monkeypatch):
    t=tiny;args=cli_args(t)[:7];args[0]='evaluate';t.out.write_bytes(b'previous result')
    args+=['--snapshot',str(t.out.parent/'unused'),'--manifest-sha256','a'*64,
           '--p0-result',str(t.out.parent/'unused-reference'),'--p0-result-sha256','b'*64]
    monkeypatch.setattr(cli,'load_snapshot',lambda *a:pytest.fail('re-evaluated existing result'))
    assert cli.main(args)==2 and t.out.read_bytes()==b'previous result'


def test_cli_bad_reference_pin_refuses_before_any_fit(tiny,cli,monkeypatch):
    t=tiny;args=cli_args(t)[:7];args[0]='evaluate'
    reference=t.out.parent/'reference.json';reference.write_bytes(b'{}')
    args+=['--snapshot',str(t.out.parent/'unused'),'--manifest-sha256','a'*64,
           '--p0-result',str(reference),'--p0-result-sha256','b'*64]
    monkeypatch.setattr(cli,'load_snapshot',lambda *a:pytest.fail('read despite wrong reference'))
    monkeypatch.setattr(cli,'evaluate_series',lambda *a:pytest.fail('fit despite wrong reference'))
    assert cli.main(args)==2 and not t.out.exists()


@pytest.mark.parametrize('status,code',[('quota_wait',3),('paused',4),('batch_request_limit',0)])
def test_cli_batch_maps_stop_status_without_daemon(tiny,cli,monkeypatch,status,code):
    def batch(cache,**kw):
        assert cache==tiny.cache and kw['max_requests']==1 and kw['max_seconds']==60
        assert kw['code_commit']=='c'*40
        kw['guard']()
        return {'status':status,'downloaded':0,'automatic_retries':0}
    monkeypatch.setattr(cli,'run_batch',batch)
    args=['batch','--cache',str(tiny.cache),'--recovery-sha256','a'*64,
          '--continuation-sha256','b'*64,'--confirm-code-commit','c'*40,
          '--max-requests','1','--max-seconds','60']
    assert cli.main(args)==code
