"""Synthetic journals only: explicit recovery of one pinned bad streaming body."""
from copy import deepcopy
from datetime import timedelta
import fcntl
import importlib.util
from pathlib import Path

import pytest

from app.services.forecasting import replay_cache_worker as c
from app.services.forecasting import replay_calendar_recovery as r
from app.services.forecasting import replay_acquisition as q
from app.services.forecasting import replay_full as full
from app.services.forecasting.replay_p0 import ReplayRefusal


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


prior = module('retry_parent_fixtures', Path(__file__).with_name('test_forecasting_replay_cache_continuation.py'))
io = module('retry_io_fixtures', Path(__file__).with_name('test_forecasting_replay_full_io.py'))
completed, tiny = prior.completed, io.tiny
NOW, OLD, NEW = prior.NOW, prior.NEW, 'd'*40


def forbidden(*a, **kw):
    pytest.fail('unexpected network/retry')


@pytest.fixture
def failed(completed, monkeypatch):
    f = completed
    _, f.cpin = prior.authorize(f)
    def validate(self, raw, request):
        if raw != b'good':
            raise ReplayRefusal('invalid or ambiguous JSON')
    monkeypatch.setattr(c.CacheWorker, 'validate', validate)
    assert prior.worker(f, f.cpin).step(now=NOW+timedelta(seconds=30),
        fetch=lambda *a:r.STREAM_ERROR, code_commit=OLD)['status'] == 'paused'
    f.failed = (f.root/'state.json').read_bytes()
    original = c.parse_json(f.failed)
    f.retry = {'schema':1, 'id':'M3-P1-CALENDAR-ONE-RETRY', 'retry_limit':1,
               'canonical_ledger':str(f.root.resolve()), 'failed_state_sha256':c.digest(f.failed),
               'failed_response_sha256':c.digest(r.STREAM_ERROR),
               'failed_day':f.policy['allowed_missing_dates'][0],
               'recovery_amendment_sha256':f.rpin, 'continuation_sha256':f.cpin,
               'execution_commit':NEW, 'allowed_missing_dates':f.policy['allowed_missing_dates'],
               'max_total_attempts':sum(e['kind']=='attempt' for e in original['events'])+2,
               'max_total_reserved_units':sum(e['weight'] for e in original['events'])+42,
               'caps':deepcopy(r.CAPS), 'scientific_changes':False, 'on_failure':'stop_no_more_retries'}
    return f


def authorize(f):
    raw = c.canonical(f.retry); pin = c.digest(raw)
    return r.authorize_once(f.root, raw, pin, f.rpin, f.cpin,
                           now=NOW+timedelta(seconds=40), code_commit=NEW), pin


def worker(f, pin):
    return r.CalendarRecoveryWorker(f.root, f.rpin, f.cpin, pin)


def state(f):
    return c.parse_json((f.root/'state.json').read_bytes())


def test_offline_authorization_archives_bytes_and_preserves_all_parent_identity(failed):
    f = failed; result, pin = authorize(f)
    assert result == {'status':'authorized_calendar_retry','network_requests':0,'retry_limit':1}
    assert (f.root/r.BACKUP).read_bytes() == f.failed
    assert (f.root/r.BAD_RESPONSE).read_bytes() == r.STREAM_ERROR
    assert not (f.root/'raw'/f'ifs-{f.retry["failed_day"]}.json').exists()
    old, new = c.parse_json(f.failed), state(f)
    for key in ('events','completed','code_commit','plan_sha256','manual_recovery'):
        assert new[key] == old[key]
    assert new['calendar_continuation'] == {**old['calendar_continuation'],'policy_sha256':pin}
    assert new['paused'] is None and new['inflight'] is None
    assert new['calendar_retry']['amendment_sha256'] == pin


def test_one_repeat_then_remaining_calendar_and_no_more_http(failed):
    f = failed; _, pin = authorize(f); w = worker(f,pin); calls = []
    def fetch(*a):
        s = state(f); event = s['events'][-1]
        assert s['events'][:len(c.parse_json(f.failed)['events'])] == c.parse_json(f.failed)['events']
        assert event['calendar_continuation_sha256'] == f.cpin
        if not calls:
            assert event['attempt_ordinal'] == 2 and event['recovery_amendment_sha256'] == pin
        calls.append(event['day']); return b'good'
    for second in (40,44):
        assert w.step(now=NOW+timedelta(seconds=second),fetch=fetch,code_commit=NEW)['status']=='downloaded'
    assert calls == f.retry['allowed_missing_dates']
    assert w.step(now=NOW+timedelta(seconds=48),fetch=forbidden,code_commit=NEW)['status']=='all_downloaded_pending_final_audit'
    assert w.audit(now=NOW+timedelta(seconds=48))['ready_for_review']
    assert (f.root/r.BAD_RESPONSE).read_bytes() == r.STREAM_ERROR
    assert sum(e['weight'] for e in state(f)['events']) == f.retry['max_total_reserved_units']


@pytest.mark.parametrize('key,value', [
    ('retry_limit',2),('retry_limit',True),('schema',True),('id','other'),
    ('canonical_ledger','/other'),('execution_commit','bad'),('failed_state_sha256','0'*64),
    ('failed_response_sha256','0'*64),('failed_day','2024-01-01'),
    ('continuation_sha256','0'*64),('recovery_amendment_sha256','0'*64),
    ('allowed_missing_dates',[]),('allowed_missing_dates',['2024-11-29','2024-11-28']),
    ('max_total_attempts',999),('max_total_reserved_units',999),
    ('caps',{'rolling60s':999}),('scientific_changes',True),('on_failure','retry')])
def test_policy_drift_refuses_before_mutation(failed,key,value):
    f = failed; f.retry[key] = value
    with pytest.raises(ValueError): authorize(f)
    assert (f.root/'state.json').read_bytes() == f.failed
    assert not (f.root/r.BACKUP).exists() and not (f.root/r.BAD_RESPONSE).exists()


def test_unknown_fields_and_wrong_external_pin_or_code_refuse(failed):
    f = failed; raw = c.canonical(f.retry)
    for pin, code in [('0'*64,NEW),(c.digest(raw),OLD)]:
        with pytest.raises(ValueError):
            r.authorize_once(f.root,raw,pin,f.rpin,f.cpin,now=NOW+timedelta(seconds=40),code_commit=code)
    f.retry['hidden_retry'] = True
    with pytest.raises(ValueError): authorize(f)
    assert (f.root/'state.json').read_bytes() == f.failed


@pytest.mark.parametrize('defect',['raw','archive','symlink'])
def test_unrecognized_body_or_archive_collision_refuses(failed,defect,tmp_path):
    f=failed;target=f.root/'raw'/f'ifs-{f.retry["failed_day"]}.json'
    if defect=='raw':target.write_bytes(b'different error or valid weather')
    elif defect=='archive':(f.root/r.BAD_RESPONSE).write_bytes(r.STREAM_ERROR)
    else:(f.root/r.BAD_RESPONSE).symlink_to(tmp_path/'missing')
    with pytest.raises(ValueError):authorize(f)
    assert (f.root/'state.json').read_bytes()==f.failed
    assert not (f.root/r.BACKUP).exists()


def test_all_three_pins_required_and_old_worker_refuses(failed):
    f=failed;_,pin=authorize(f)
    for w in (c.CacheWorker(f.root,recovery_pin=f.rpin,continuation_pin=f.cpin),
              r.CalendarRecoveryWorker(f.root,None,f.cpin,pin),
              r.CalendarRecoveryWorker(f.root,f.rpin,None,pin),worker(f,None),worker(f,'0'*64)):
        with pytest.raises(ValueError):w.step(now=NOW+timedelta(seconds=41),fetch=forbidden,code_commit=NEW)
    with pytest.raises(ValueError,match='execution code'):
        worker(f,pin).step(now=NOW+timedelta(seconds=41),fetch=forbidden,code_commit=OLD)


def test_frozen_original_continuation_validator_refuses_even_before_retry(failed):
    # This is the unchanged validator present in frozen040a04bb, not merely
    # the new base-worker guard. Stale code must fail before a duplicate GET.
    from app.services.forecasting.replay_cache_continuation import validate_active
    f=failed;authorize(f)
    requests={row['issue_date']:row for row in f.plan['requests']}
    with pytest.raises(ValueError,match='authorization differs'):
        validate_active(f.root,state(f),f.cpin,f.rpin,NOW+timedelta(seconds=41),requests)


def test_second_failure_reauthorization_and_later_step_never_retry(failed):
    f=failed;_,pin=authorize(f);w=worker(f,pin)
    assert w.step(now=NOW+timedelta(seconds=40),fetch=lambda *a:r.STREAM_ERROR,code_commit=NEW)['status']=='paused'
    before=(f.root/'state.json').read_bytes()
    assert authorize(f)[0]['status']=='already_authorized'
    assert (f.root/'state.json').read_bytes()==before
    assert w.step(now=NOW+timedelta(days=1),fetch=forbidden,code_commit=NEW)['status']=='paused'
    assert len(state(f)['events'])==len(c.parse_json(f.failed)['events'])+1


@pytest.mark.parametrize('moment',['rename','sync','save'])
def test_offline_authorization_crash_resumes_without_network_or_data_loss(failed,monkeypatch,moment):
    f=failed
    target={'rename':(r.os,'rename'),'sync':(r,'sync_dir'),'save':(r,'save_state')}[moment]
    real=getattr(*target)
    def fail(*a,**kw):raise OSError('synthetic disk failure')
    monkeypatch.setattr(*target,fail)
    with pytest.raises(OSError):authorize(f)
    assert (f.root/'state.json').read_bytes()==f.failed
    assert (f.root/r.BACKUP).read_bytes()==f.failed
    raw_path=f.root/'raw'/f'ifs-{f.retry["failed_day"]}.json'
    assert (raw_path if raw_path.exists() else f.root/r.BAD_RESPONSE).read_bytes()==r.STREAM_ERROR
    monkeypatch.setattr(*target,real)
    assert authorize(f)[0]['network_requests']==0


@pytest.mark.parametrize('defect',['backup','archive','policy','old_events','old_receipt','identity','new_order','forged_completion'])
def test_active_tamper_is_rejected_before_fetch(failed,defect):
    f=failed;_,pin=authorize(f);s=state(f)
    if defect=='backup':(f.root/r.BACKUP).write_bytes(b'{}')
    elif defect=='archive':(f.root/r.BAD_RESPONSE).write_bytes(b'changed')
    elif defect=='policy':(f.root/r.AMENDMENT).write_bytes(b'{}')
    else:
        if defect=='old_events':s['events'][0]['weight']=1
        elif defect=='old_receipt':next(iter(s['completed'].values()))['source']['extra']=True
        elif defect=='identity':s['calendar_continuation']['enabled_at']=NOW.isoformat()
        elif defect=='new_order':s['events'].append({'kind':'attempt','day':f.retry['allowed_missing_dates'][1],'at':(NOW+timedelta(seconds=40)).isoformat(),'weight':21})
        else:s['completed'][f.retry['failed_day']]=deepcopy(next(iter(s['completed'].values())))
        c.save_state(f.root,s)
    with pytest.raises((ValueError,OSError)):
        worker(f,pin).step(now=NOW+timedelta(seconds=41),fetch=forbidden,code_commit=NEW)


def test_lock_and_quota_do_not_spend_an_extra_attempt(failed,monkeypatch):
    f=failed
    with (f.root/'worker.lock').open('rb') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(ValueError,match='busy'):authorize(f)
    _,pin=authorize(f);w=worker(f,pin);before=state(f)['events']
    later=NOW+timedelta(days=1)
    monkeypatch.setattr(w,'wait_until',lambda *a:later)
    assert w.step(now=NOW+timedelta(seconds=40),fetch=forbidden,code_commit=NEW)['status']=='quota_wait'
    assert state(f)['events']==before


def test_completion_clock_keeps_four_second_spacing(failed):
    f=failed;_,pin=authorize(f);w=worker(f,pin)
    times=iter([NOW+timedelta(seconds=40),NOW+timedelta(seconds=60)])
    assert w.step(now=NOW+timedelta(seconds=40),fetch=lambda *a:b'good',code_commit=NEW,clock=lambda:next(times))['status']=='downloaded'
    assert w.step(now=NOW+timedelta(seconds=61),fetch=forbidden,code_commit=NEW)=={
        'status':'quota_wait','not_before':(NOW+timedelta(seconds=64)).isoformat()}


def test_bounded_controller_integrates_third_pin_without_automatic_retry(failed):
    f=failed;_,pin=authorize(f)
    result=q.run_batch(f.root,recovery_pin=f.rpin,continuation_pin=f.cpin,calendar_retry_pin=pin,
        code_commit=NEW,max_requests=1,max_seconds=60,fetch=lambda *a:b'good',
        clock=lambda:NOW+timedelta(seconds=40),monotonic=lambda:0.0,sleep=forbidden,guard=lambda:None)
    assert result=={'status':'batch_request_limit','downloaded':1,'automatic_retries':0}


def test_cli_authorizes_offline_and_status_requires_third_pin(failed,monkeypatch,capsys,tmp_path):
    f=failed;cli=module('retry_cli',Path(__file__).parents[1]/'scripts/forecast_replay_cache_worker.py')
    class Clock:
        @staticmethod
        def now(zone):return NOW+timedelta(seconds=40)
    monkeypatch.setattr(cli,'datetime',Clock);monkeypatch.setattr(cli,'frozen_head',lambda:NEW)
    monkeypatch.setattr(cli,'bounded_fetch',forbidden)
    raw=c.canonical(f.retry);pin=c.digest(raw);p=tmp_path/'amendment.json';p.write_bytes(raw)
    args=['authorize-calendar-retry',str(f.root),'--amendment',str(p),'--amendment-sha256',pin,
          '--recovery-sha256',f.rpin,'--continuation-sha256',f.cpin,'--confirm-code-commit',NEW]
    assert cli.main(args[:-1]+[OLD])==2
    assert cli.main(args)==0
    assert c.parse_json(capsys.readouterr().out.encode())['network_requests']==0
    args=['status',str(f.root),'--recovery-sha256',f.rpin,'--continuation-sha256',f.cpin]
    assert cli.main(args)==2
    assert cli.main(args+['--calendar-retry-sha256',pin])==0


@pytest.mark.parametrize('missing',['calendar_retry_sha256','calendar_retry_execution_commit'])
def test_scientific_protocol_requires_both_retry_pin_and_source(tiny,missing):
    p=tiny.p;p['calendar_retry_sha256']='a'*64;p['calendar_retry_execution_commit']=NEW
    full.validate_protocol(p);p.pop(missing)
    with pytest.raises(ValueError,match='retry'):full.validate_protocol(p)


@pytest.mark.parametrize('defect',[None,'source','undisclosed'])
def test_export_discloses_pinned_retry_source_without_changing_science(tiny,monkeypatch,defect):
    t=tiny;raw=c.canonical({'execution_commit':NEW});(t.cache/r.AMENDMENT).write_bytes(raw)
    t.p.update(calendar_retry_sha256=c.digest(raw),calendar_retry_execution_commit=NEW)
    t.state['calendar_retry']={'amendment_sha256':c.digest(raw)}
    monkeypatch.setattr(r,'CalendarRecoveryWorker',full.CacheWorker)
    if defect=='source':t.p['calendar_retry_execution_commit']='e'*40
    if defect=='undisclosed':
        t.p.pop('calendar_retry_sha256');t.p.pop('calendar_retry_execution_commit')
    if defect:
        with pytest.raises(ValueError,match='retry'):io.export(t)
    else:
        pin=io.export(t);full.load_snapshot(t.out,t.p,pin)


def test_full_cli_forwards_explicit_retry_pin_without_changing_batch_bounds(failed,monkeypatch):
    f=failed;_,pin=authorize(f)
    cli=module('retry_full_cli',Path(__file__).parents[1]/'scripts/forecast_replay_full.py')
    monkeypatch.setattr(cli,'frozen_head',lambda:NEW)
    monkeypatch.setattr(cli,'bounded_fetch',forbidden)
    def run(root,**kw):
        assert root==f.root and kw['calendar_retry_pin']==pin
        assert kw['recovery_pin']==f.rpin and kw['continuation_pin']==f.cpin
        assert kw['max_requests']==1 and kw['max_seconds']==60
        kw['guard']()
        return {'status':'paused','downloaded':0,'automatic_retries':0}
    monkeypatch.setattr(cli,'run_batch',run)
    assert cli.main(['batch','--cache',str(f.root),'--recovery-sha256',f.rpin,
        '--continuation-sha256',f.cpin,'--calendar-retry-sha256',pin,
        '--confirm-code-commit',NEW,'--max-requests','1','--max-seconds','60'])==4
