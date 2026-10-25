"""Synthetic-only stage1 contract and leakage tests; no HTTP or project data."""
from copy import deepcopy
from datetime import date,timedelta
import math
import pytest
from app.services.forecasting import replay_stage1 as s

@pytest.fixture
def protocol():
 return {'id':'M3-P1-STAGE1-2026-10-22','origin':s.ORIGIN.isoformat(),'training_runs':180,
 'training_issue_start':str(s.ISSUES[0]),'training_issue_end':str(s.ISSUES[-1]),
 'points':21,'horizon':7,'methods':list(s.METHODS),'promotion_allowed':False,
 'previous_outcome_exposure':True,'p0_manifest_sha256':s.P0_MANIFEST_PIN,
 'parent_plan_sha256':'909ef2009f7f9fc6128365bad38e14d9ebee2a744861b7e43afd56eea67e9ffa'}

@pytest.mark.parametrize('key,value',[
 ('id','other'),('origin','2025-01-31T23:59:59+00:00'),('training_runs',179),
 ('training_issue_start','2024-06-28'),('training_issue_end','2024-12-24'),
 ('points',20),('horizon',6),('methods',list(s.METHODS[:-1])),('promotion_allowed',True),
 ('previous_outcome_exposure',False),('p0_manifest_sha256','0'*64),('parent_plan_sha256','0'*64)])
def test_protocol_cannot_change_frozen_contract(protocol,key,value):
 protocol[key]=value
 with pytest.raises(ValueError):s.validate_protocol(protocol)

def test_fixed_calendar_has_180_mature_days(protocol):
 s.validate_protocol(protocol)
 assert len(set(s.ISSUES))==180
 assert s.ISSUES[-1]+timedelta(days=8)<s.ORIGIN.date()+timedelta(days=1)

@pytest.fixture(scope='module')
def series():
 start=date(2022,1,1);end=date(2025,1,7)
 days=[start+timedelta(days=i) for i in range((end-start).days+1)]
 actual={c:{d:float(10+idx*0.2+math.sin((d-start).days/7)+0.002*(d-start).days) for d in days} for idx,c in enumerate(s.POINTS)}
 forecasts={issue:{c:{issue+timedelta(days=h):actual[c][issue+timedelta(days=h)]+2+h/10 for h in range(1,8)} for c in s.POINTS} for issue in s.ISSUES}
 test={c:{s.ORIGIN.date()+timedelta(days=h):actual[c][s.ORIGIN.date()+timedelta(days=h)]+2+h/10 for h in range(1,8)} for c in s.POINTS}
 return actual,forecasts,test

@pytest.fixture(scope='module')
def scored(series):
 # Explicit protocol duplication is a synthetic fixture, never a real result.
 protocol={'id':'M3-P1-STAGE1-2026-10-22','origin':s.ORIGIN.isoformat(),'training_runs':180,'training_issue_start':str(s.ISSUES[0]),'training_issue_end':str(s.ISSUES[-1]),'points':21,'horizon':7,'methods':list(s.METHODS),'promotion_allowed':False,'previous_outcome_exposure':True,'p0_manifest_sha256':s.P0_MANIFEST_PIN,'parent_plan_sha256':'909ef2009f7f9fc6128365bad38e14d9ebee2a744861b7e43afd56eea67e9ffa'}
 return s.evaluate_series(*series,protocol)

def test_complete_synthetic_all_methods_and_points(scored):
 assert scored['test_targets']==147 and scored['calibration_pairs']==26460
 assert scored['calibrated_cells']==147 and scored['raw_fallback_cells']==0
 assert set(scored['methods'])==set(s.METHODS)
 assert not scored['promotion_allowed']
 for name, result in scored['methods'].items():
  assert set(result['by_point'])==set(s.POINTS)
  assert all(math.isfinite(v) for v in result['macro'].values())
 assert scored['methods'][s.CAL]['macro']['mae']<1e-12
 assert scored['methods'][s.RAW]['macro']['mae']==pytest.approx(2.4)
 assert all(c['eligible_pairs']==180 and c['last_target_end']<=s.ORIGIN.isoformat() for cs in scored['corrections'].values() for c in cs)

def test_all_metrics_recompute_from_vectors(scored):
 for c in s.POINTS:
  for name in s.METHODS:
   errors=[r['actual_minus_predicted_c'] for r in scored['daily_predictions'][c][name]]
   expect={'mae':sum(abs(e) for e in errors)/7,'rmse':math.sqrt(sum(e*e for e in errors)/7),'bias':sum(errors)/7}
   expect['mase']=expect['mae']/scored['mase_scales'][c]
   for key,value in expect.items():assert scored['methods'][name]['by_point'][c][key]==pytest.approx(value)

def test_future_labels_do_not_affect_fitted_corrections_or_predictions(series,scored,protocol):
 actual,forecasts,test=deepcopy(series)
 for c in s.POINTS:
  for h in range(1,8):actual[c][s.ORIGIN.date()+timedelta(days=h)]+=100
 changed=s.evaluate_series(actual,forecasts,test,protocol)
 assert changed['corrections']==scored['corrections']
 for c in s.POINTS:
  for name in s.METHODS:
   assert [r['prediction_c'] for r in changed['daily_predictions'][c][name]]==[r['prediction_c'] for r in scored['daily_predictions'][c][name]]
 assert changed['methods'][s.CAL]['macro']['mae']==pytest.approx(100)

@pytest.mark.parametrize('case',['point','issue','target','train_point','train_target','actual_target','actual_gap','nan','bool'])
def test_missing_or_invalid_grid_refuses(series,protocol,case):
 a,f,t=deepcopy(series);c=s.POINTS[0];issue=s.ISSUES[0];target=issue+timedelta(days=1)
 if case=='point':del a[c]
 elif case=='issue':del f[issue]
 elif case=='target':del t[c][date(2025,1,1)]
 elif case=='train_point':del f[issue][c]
 elif case=='train_target':del f[issue][c][target]
 elif case=='actual_target':del a[c][target]
 elif case=='actual_gap':del a[c][date(2022,4,3)]
 elif case=='nan':a[c][date(2022,4,3)]=float('nan')
 elif case=='bool':a[c][date(2022,4,3)]=False
 with pytest.raises(ValueError):s.evaluate_series(a,f,t,protocol)

def test_external_manifest_pin_before_any_payload(tmp_path,protocol):
 (tmp_path/'manifest.json').write_bytes(b'{}')
 with pytest.raises(ValueError,match='external manifest'):s.load_snapshot(tmp_path,protocol,'0'*64)

@pytest.fixture
def export_toy(tmp_path,monkeypatch):
 """Atomicity-only tiny double; real parser/science covered separately."""
 from contextlib import contextmanager
 from types import SimpleNamespace
 from app.services.forecasting.replay_p0 import canonical,digest
 issue=date(2024,6,27);cache=tmp_path/'cache';(cache/'raw').mkdir(parents=True)
 p0=tmp_path/'p0';p0.mkdir();p0protocol=tmp_path/'p0-protocol.json';p0protocol.write_bytes(b'{}')
 raw=b'synthetic atomicity fixture';name=f'ifs-{issue}.json';(cache/'raw'/name).write_bytes(raw)
 (cache/'state.json').write_bytes(b'{}')
 state={'paused':None,'inflight':None,'code_commit':'a'*40,'completed':{str(issue):{'sha256':digest(raw),'bytes':len(raw),'source':{'kind':'import'}}}}
 manifest={'entries':[]}
 for fname in ['verification.json','forecast-00.json']:
  (p0/fname).write_bytes(raw);manifest['entries'].append({'file':fname,'sha256':digest(raw),'bytes':len(raw)})
 class TinyWorker:
  def __init__(self,root):pass
  @contextmanager
  def locked(self,now):yield state,None,None
  def validate(self,*args):pass
 monkeypatch.setattr(s,'CacheWorker',TinyWorker);monkeypatch.setattr(s,'ISSUES',(issue,))
 monkeypatch.setattr(s,'P0_PROTOCOL_PIN',digest(b'{}'));monkeypatch.setattr(s,'validate_protocol',lambda p:None)
 monkeypatch.setattr(s,'read_snapshot',lambda *a:(manifest,'pin',{}))
 p={'canonical_ledger':str(cache.resolve()),'worker_commit':'a'*40,'partial_cache_expected_total':1}
 return SimpleNamespace(cache=cache,p0=p0,p0protocol=p0protocol,protocol=p,out=tmp_path/'export',state=state)

def export(toy):return s.export_snapshot(toy.cache,toy.p0,toy.p0protocol,toy.protocol,toy.out)

def test_export_members_and_manifest_written_before_atomic_rename(export_toy,monkeypatch):
 toy=export_toy;events=[];rename=s.os.rename;sync=s.sync_dir
 def observed_rename(src,dst):
  assert (src/'manifest.json').is_file() and len(list(src.iterdir()))==4
  events.append('rename');return rename(src,dst)
 def observed_sync(p):events.append('sync');return sync(p)
 monkeypatch.setattr(s.os,'rename',observed_rename);monkeypatch.setattr(s,'sync_dir',observed_sync)
 pin=export(toy)
 assert s.digest((toy.out/'manifest.json').read_bytes())==pin
 assert not toy.out.with_name('export.partial').exists()
 assert events==['rename','sync']

@pytest.mark.parametrize('stage',['raw','manifest','rename','after_rename'])
def test_export_failure_preserves_artifacts(export_toy,monkeypatch,stage):
 toy=export_toy;write=s.write_new
 def failed_write(p,raw):
  if (stage=='raw' and p.name.startswith('ifs')) or (stage=='manifest' and p.name=='manifest.json'):raise OSError('injected')
  return write(p,raw)
 monkeypatch.setattr(s,'write_new',failed_write)
 if stage=='rename':monkeypatch.setattr(s.os,'rename',lambda *a:(_ for _ in ()).throw(OSError('injected')))
 if stage=='after_rename':monkeypatch.setattr(s,'sync_dir',lambda *a:(_ for _ in ()).throw(OSError('injected')))
 with pytest.raises(OSError) as exc:export(toy)
 if stage=='after_rename':
  assert toy.out.exists() and 'durability is unconfirmed' in str(exc.value)
 else:assert not toy.out.exists() and toy.out.with_name('export.partial').is_dir()
 assert (toy.cache/'raw'/f'ifs-{s.ISSUES[0]}.json').exists()

@pytest.mark.parametrize('case',['inflight','paused','extent','source','existing','symlink'])
def test_export_refuses_unsettled_or_conflicting_state(export_toy,monkeypatch,case):
 toy=export_toy
 if case=='inflight':toy.state['inflight']={'day':'uncertain'}
 elif case=='paused':toy.state['paused']='failure'
 elif case=='extent':toy.protocol['partial_cache_expected_total']=2
 elif case=='source':(toy.cache/'raw'/f'ifs-{s.ISSUES[0]}.json').write_bytes(b'mutated')
 elif case=='existing':toy.out.mkdir();(toy.out/'user-file').write_text('keep')
 else:toy.out.symlink_to(toy.p0,target_is_directory=True)
 with pytest.raises(ValueError):export(toy)
 if case=='existing':assert (toy.out/'user-file').read_text()=='keep'
 if case=='symlink':assert toy.out.is_symlink()

def test_canonical_cache_path_cannot_be_substituted(export_toy):
 toy=export_toy;toy.protocol['canonical_ledger']='/not-this-cache'
 with pytest.raises(ValueError,match='noncanonical'):export(toy)

@pytest.fixture
def cli(monkeypatch):
    import importlib.util
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    spec=importlib.util.spec_from_file_location('stage1_cli',root/'scripts/forecast_replay_stage1.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module.subprocess,'check_output',lambda args,**kw: b'' if 'status' in args else 'a'*40)
    return module


def cli_args(tmp_path,protocol,pin=None):
    import json,hashlib
    raw=json.dumps(protocol).encode();path=tmp_path/'protocol.json';path.write_bytes(raw)
    return ['export','--protocol',str(path),'--protocol-sha256',pin or hashlib.sha256(raw).hexdigest(),'--cache',str(tmp_path/'cache'),'--p0',str(tmp_path/'p0'),'--output',str(tmp_path/'export')]


def test_cli_rejects_protocol_pin_before_export(cli,tmp_path,protocol,monkeypatch,capsys):
    monkeypatch.setattr(cli,'export_snapshot',lambda *a:pytest.fail('export after wrong pin'))
    assert cli.main(cli_args(tmp_path,protocol,'0'*64))==2
    assert 'refused' in capsys.readouterr().err


def test_cli_requires_clean_source(cli,tmp_path,protocol,monkeypatch):
    monkeypatch.setattr(cli.subprocess,'check_output',lambda *a,**kw:b' M changed.py')
    monkeypatch.setattr(cli,'export_snapshot',lambda *a:pytest.fail('export from dirty source'))
    assert cli.main(cli_args(tmp_path,protocol))==2


def test_cli_reports_postrename_uncertainty(cli,tmp_path,protocol,monkeypatch,capsys):
    def fail_after_publish(*a):
        (tmp_path/'export').mkdir()
        raise OSError('injected durability failure')
    monkeypatch.setattr(cli,'export_snapshot',fail_after_publish)
    assert cli.main(cli_args(tmp_path,protocol))==2
    import json
    message=json.loads(capsys.readouterr().err)
    assert message['snapshot_directory_exists'] is True and 'unconfirmed' in message['durability']
    assert str(tmp_path) not in str(message)


def test_cli_success_is_explicit_and_no_acquisition(cli,tmp_path,protocol,monkeypatch,capsys):
    monkeypatch.setattr(cli,'export_snapshot',lambda *a:'a'*64)
    assert cli.main(cli_args(tmp_path,protocol))==0
    import json
    message=json.loads(capsys.readouterr().out)
    assert message['status']=='exported' and message['full_cache_complete'] is False
