"""Synthetic journals and transports; no external requests or weather fitting."""
from copy import deepcopy
from datetime import timedelta
import fcntl
import importlib.util
from pathlib import Path

import pytest

from app.services.forecasting import replay_cache_worker as c
from app.services.forecasting import replay_transport_recovery as r
from app.services.forecasting.replay_calendar_recovery import CalendarRecoveryWorker


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


parent = module('transport_parent_fixtures', Path(__file__).with_name('test_forecasting_replay_calendar_recovery.py'))
completed, failed = parent.completed, parent.failed
NOW, OLD, NEW = parent.NOW, parent.NEW, 'e' * 40


def forbidden(*args, **kwargs):
    pytest.fail('unexpected transport or second retry')


@pytest.fixture
def transport_failed(failed):
    f = failed
    _, f.calendar_pin = parent.authorize(f)
    w = parent.worker(f, f.calendar_pin)
    assert w.step(now=NOW+timedelta(seconds=40), fetch=lambda *a: b'good', code_commit=OLD)['status'] == 'downloaded'

    def fail(*args):
        raise c.TransportFailure(error_class='URLError', returncode=2)

    assert w.step(now=NOW+timedelta(seconds=44), fetch=fail, code_commit=OLD)['status'] == 'paused'
    f.transport_failed = (f.root/'state.json').read_bytes()
    state = c.parse_json(f.transport_failed)
    day = state['inflight']['day']
    f.transport_policy = {
        'schema': 1, 'id': 'M3-P1-TRANSPORT-ONE-RETRY', 'retry_limit': 1,
        'canonical_ledger': str(f.root.resolve()), 'failed_state_sha256': c.digest(f.transport_failed),
        'failed_day': day, 'allowed_missing_dates': [day], 'execution_commit': NEW,
        'recovery_amendment_sha256': f.rpin, 'continuation_sha256': f.cpin,
        'calendar_retry_sha256': f.calendar_pin, 'caps': deepcopy(r.CAPS),
        'max_total_attempts': sum(e['kind']=='attempt' for e in state['events'])+1,
        'max_total_reserved_units': sum(e['weight'] for e in state['events'])+21,
        'scientific_changes': False, 'on_failure': 'stop_no_more_retries',
    }
    return f


def authorize(f, seconds=50):
    raw = c.canonical(f.transport_policy)
    pin = c.digest(raw)
    return r.authorize_once(f.root, raw, pin, f.rpin, f.cpin, f.calendar_pin,
                           now=NOW+timedelta(seconds=seconds), code_commit=NEW), pin


def worker(f, pin):
    return r.TransportRetryWorker(f.root, f.rpin, f.cpin, f.calendar_pin, pin)


def state(f):
    return c.parse_json((f.root/'state.json').read_bytes())


def test_authorization_preserves_all_history_and_only_clears_the_reviewed_pause(transport_failed):
    f = transport_failed
    result, pin = authorize(f)
    assert result == {'status':'authorized_transport_retry','network_requests':0,'retry_limit':1}
    assert (f.root/r.BACKUP).read_bytes() == f.transport_failed
    before, after = c.parse_json(f.transport_failed), state(f)
    for key in ('events','completed','code_commit','plan_sha256','manual_recovery','calendar_retry','failure_diagnostic'):
        assert after[key] == before[key]
    assert after['paused'] is None and after['inflight'] is None
    assert after['calendar_continuation'] == {**before['calendar_continuation'],'policy_sha256':pin}


def test_exactly_one_date_one_attempt_then_stop_without_calendar_continuation(transport_failed):
    f = transport_failed
    _, pin = authorize(f)
    calls = []

    def fetch(*args):
        s = state(f)
        assert s['events'][-1]['attempt_ordinal'] == 2
        assert s['events'][-1]['recovery_amendment_sha256'] == pin
        calls.append(s['events'][-1]['day'])
        return b'good'

    w = worker(f,pin)
    assert w.step(now=NOW+timedelta(seconds=50),fetch=fetch,code_commit=NEW)['status']=='downloaded'
    assert calls == [f.transport_policy['failed_day']]
    assert w.step(now=NOW+timedelta(seconds=60),fetch=forbidden,code_commit=NEW)['status']==r.FINISHED
    assert authorize(f)[0]['status']=='already_authorized'
    assert sum(e['weight'] for e in state(f)['events'])==f.transport_policy['max_total_reserved_units']


def test_failure_and_reauthorization_never_allow_another_attempt(transport_failed):
    f = transport_failed
    _, pin = authorize(f)

    def fail(*args):
        raise c.TransportFailure(error_class='URLError',returncode=2)

    w=worker(f,pin)
    assert w.step(now=NOW+timedelta(seconds=50),fetch=fail,code_commit=NEW)['status']=='paused'
    before=(f.root/'state.json').read_bytes()
    assert authorize(f)[0]['status']=='already_authorized'
    assert (f.root/'state.json').read_bytes()==before
    assert w.step(now=NOW+timedelta(days=1),fetch=forbidden,code_commit=NEW)['status']=='paused'


@pytest.mark.parametrize('key,value',[
    ('schema',True),('retry_limit',2),('retry_limit',True),('id','other'),
    ('canonical_ledger','/other'),('failed_day','2020-01-01'),('allowed_missing_dates',[]),
    ('allowed_missing_dates',['2024-11-29','2024-11-30']),('failed_state_sha256','0'*64),
    ('recovery_amendment_sha256','0'*64),('continuation_sha256','0'*64),
    ('calendar_retry_sha256','0'*64),('execution_commit','bad'),
    ('max_total_attempts',999),('max_total_reserved_units',999),
    ('caps',{'rolling86400s':999999}),('scientific_changes',True),('on_failure','retry')])
def test_policy_drift_refuses_before_mutation(transport_failed,key,value):
    f=transport_failed;f.transport_policy[key]=value
    with pytest.raises(ValueError):authorize(f)
    assert (f.root/'state.json').read_bytes()==f.transport_failed
    assert not (f.root/r.BACKUP).exists()


def test_unknown_fields_pin_and_source_refuse(transport_failed):
    f=transport_failed;raw=c.canonical(f.transport_policy)
    for pin,code in [('0'*64,NEW),(c.digest(raw),OLD)]:
        with pytest.raises(ValueError):
            r.authorize_once(f.root,raw,pin,f.rpin,f.cpin,f.calendar_pin,
                             now=NOW+timedelta(seconds=50),code_commit=code)
    f.transport_policy['hidden_retry']=True
    with pytest.raises(ValueError):authorize(f)
    assert (f.root/'state.json').read_bytes()==f.transport_failed


@pytest.mark.parametrize('defect',['raw','raw_symlink','backup_collision','policy_collision'])
def test_unexpected_artefacts_refuse(transport_failed,defect,tmp_path):
    f=transport_failed;target=f.root/'raw'/f'ifs-{f.transport_policy["failed_day"]}.json'
    if defect=='raw':target.write_bytes(b'unreviewed')
    elif defect=='raw_symlink':target.symlink_to(tmp_path/'missing')
    elif defect=='backup_collision':(f.root/r.BACKUP).write_bytes(b'wrong')
    else:(f.root/r.AMENDMENT).write_bytes(b'wrong')
    with pytest.raises((ValueError,OSError)):authorize(f)
    assert (f.root/'state.json').read_bytes()==f.transport_failed


def test_all_parent_pins_and_old_workers_refuse(transport_failed):
    f=transport_failed;_,pin=authorize(f)
    for w in (CalendarRecoveryWorker(f.root,f.rpin,f.cpin,f.calendar_pin),worker(f,None),
              worker(f,'0'*64),r.TransportRetryWorker(f.root,None,f.cpin,f.calendar_pin,pin),
              r.TransportRetryWorker(f.root,f.rpin,None,f.calendar_pin,pin),
              r.TransportRetryWorker(f.root,f.rpin,f.cpin,None,pin)):
        with pytest.raises(ValueError):w.step(now=NOW+timedelta(seconds=51),fetch=forbidden,code_commit=NEW)
    with pytest.raises(ValueError):worker(f,pin).step(now=NOW+timedelta(seconds=51),fetch=forbidden,code_commit=OLD)


@pytest.mark.parametrize('defect',['backup','policy','old_event','old_receipt','parent','new_day','new_ordinal','new_time','extra_attempt','forged_completion'])
def test_active_tamper_refuses_before_fetch(transport_failed,defect):
    f=transport_failed;_,pin=authorize(f);s=state(f)
    if defect=='backup':(f.root/r.BACKUP).write_bytes(b'{}')
    elif defect=='policy':(f.root/r.AMENDMENT).write_bytes(b'{}')
    else:
        if defect=='old_event':s['events'][0]['weight']+=1
        elif defect=='old_receipt':next(iter(s['completed'].values()))['source']['extra']=True
        elif defect=='parent':s['calendar_retry']['enabled_at']=NOW.isoformat()
        elif defect=='forged_completion':s['completed'][f.transport_policy['failed_day']]=deepcopy(next(iter(s['completed'].values())))
        else:
            event={'kind':'attempt','day':f.transport_policy['failed_day'],'weight':21,
                   'at':(NOW+timedelta(seconds=50)).isoformat(),'reserved_at':(NOW+timedelta(seconds=50)).isoformat(),
                   'calendar_continuation_sha256':f.cpin,'attempt_ordinal':2,'recovery_amendment_sha256':pin}
            if defect=='new_day':event['day']='2024-11-28'
            if defect=='new_ordinal':event['attempt_ordinal']=3
            if defect=='new_time':event['reserved_at']=(NOW+timedelta(seconds=49)).isoformat()
            s['events'].append(event)
            if defect=='extra_attempt':s['events'].append(deepcopy(event))
        c.save_state(f.root,s)
    with pytest.raises((ValueError,OSError)):worker(f,pin).step(now=NOW+timedelta(seconds=51),fetch=forbidden,code_commit=NEW)


def test_quota_and_lock_do_not_consume_the_authorized_attempt(transport_failed,monkeypatch):
    f=transport_failed
    with (f.root/'worker.lock').open('rb') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(ValueError):authorize(f)
    _,pin=authorize(f);w=worker(f,pin);before=state(f)['events']
    monkeypatch.setattr(w,'wait_until',lambda *a:NOW+timedelta(days=1))
    assert w.step(now=NOW+timedelta(seconds=50),fetch=forbidden,code_commit=NEW)['status']=='quota_wait'
    assert state(f)['events']==before


def test_crash_before_state_save_is_resumable_offline(transport_failed,monkeypatch):
    f=transport_failed;real=r.save_state
    def fail(*a):raise OSError('synthetic disk failure')
    monkeypatch.setattr(r,'save_state',fail)
    with pytest.raises(OSError):authorize(f)
    assert (f.root/'state.json').read_bytes()==f.transport_failed
    assert (f.root/r.BACKUP).read_bytes()==f.transport_failed
    monkeypatch.setattr(r,'save_state',real)
    assert authorize(f)[0]['network_requests']==0


def test_crash_after_reservation_cannot_issue_another_get(transport_failed):
    f=transport_failed;_,pin=authorize(f);w=worker(f,pin)
    def interrupt(*args):raise SystemExit('synthetic abrupt process termination')
    with pytest.raises(SystemExit):
        w.step(now=NOW+timedelta(seconds=50),fetch=interrupt,code_commit=NEW)
    assert state(f)['inflight']['day']==f.transport_policy['failed_day']
    assert w.step(now=NOW+timedelta(seconds=60),fetch=forbidden,code_commit=NEW)['status']=='paused'
    assert 'uncertain_attempt_without_raw' in state(f)['paused']
    assert authorize(f,seconds=70)[0]['status']=='already_authorized'


@pytest.mark.parametrize('defect',['diagnostic','returncode','pause','response_identity'])
def test_other_failure_shapes_refuse_before_authorization(transport_failed,defect):
    f=transport_failed;s=state(f)
    if defect=='diagnostic':s['failure_diagnostic']['child_error_class']='TimeoutError'
    elif defect=='returncode':s['failure_diagnostic']['child_returncode']=1
    elif defect=='pause':s['paused']='failure_completion_clock_uncertain; review required'
    else:s['inflight']['response_sha256']='0'*64
    c.save_state(f.root,s);before=(f.root/'state.json').read_bytes()
    f.transport_policy['failed_state_sha256']=c.digest(before)
    with pytest.raises(ValueError):authorize(f)
    assert (f.root/'state.json').read_bytes()==before


def test_new_completion_provenance_cannot_be_relabelled(transport_failed):
    f=transport_failed;_,pin=authorize(f);w=worker(f,pin)
    assert w.step(now=NOW+timedelta(seconds=50),fetch=lambda *a:b'good',code_commit=NEW)['status']=='downloaded'
    s=state(f);s['completed'][f.transport_policy['failed_day']]['source']={'kind':'import'}
    c.save_state(f.root,s)
    with pytest.raises(ValueError,match='provenance'):
        w.step(now=NOW+timedelta(seconds=60),fetch=forbidden,code_commit=NEW)


def test_pinned_raw_crash_recovers_without_http_and_remains_readable(transport_failed,monkeypatch):
    f=transport_failed;_,pin=authorize(f);w=worker(f,pin);real=w.complete
    def interrupt(*args,**kwargs):raise SystemExit('synthetic crash after saved raw')
    monkeypatch.setattr(w,'complete',interrupt)
    with pytest.raises(SystemExit):
        w.step(now=NOW+timedelta(seconds=50),fetch=lambda *a:b'good',code_commit=NEW)
    assert state(f)['inflight']['response_sha256']==c.digest(b'good')
    monkeypatch.setattr(w,'complete',real)
    assert w.step(now=NOW+timedelta(seconds=60),fetch=forbidden,code_commit=NEW)['status']=='recovered_without_http'
    assert state(f)['completed'][f.transport_policy['failed_day']]['source']['recovered_without_http'] is True
    assert w.step(now=NOW+timedelta(seconds=61),fetch=forbidden,code_commit=NEW)['status']==r.FINISHED
    assert authorize(f,seconds=70)[0]['status']=='already_authorized'


def test_cli_offline_authorization_and_single_fetch_forwarding(transport_failed,monkeypatch,tmp_path,capsys):
    f=transport_failed;cli=module('transport_cli',Path(__file__).parents[1]/'scripts/forecast_replay_transport_retry.py')
    class Clock:
        @staticmethod
        def now(zone):return NOW+timedelta(seconds=50)
    monkeypatch.setattr(cli,'datetime',Clock);monkeypatch.setattr(cli,'frozen_head',lambda:NEW)
    monkeypatch.setattr(cli,'bounded_fetch',lambda *a:b'good')
    raw=c.canonical(f.transport_policy);pin=c.digest(raw);p=tmp_path/'policy.json';p.write_bytes(raw)
    args=['--cache',str(f.root),'--recovery-sha256',f.rpin,'--continuation-sha256',f.cpin,
          '--calendar-retry-sha256',f.calendar_pin,'--transport-retry-sha256',pin,'--confirm-code-commit',NEW]
    assert cli.main(['authorize',*args,'--amendment',str(p)])==0
    assert cli.main(['fetch-one',*args])==0
    monkeypatch.setattr(cli,'bounded_fetch',forbidden)
    assert cli.main(['fetch-one',*args])==0
    assert r.FINISHED in capsys.readouterr().out
