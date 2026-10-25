"""Fake existing error responses only; no real child or network is started."""
import base64
from copy import deepcopy
from datetime import timedelta
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from app.services.forecasting import replay_cache_worker as c
from app.services.forecasting import replay_p0

spec=importlib.util.spec_from_file_location('diagnostic_worker_fixtures',Path(__file__).with_name('test_forecasting_replay_cache_worker.py'))
prior=importlib.util.module_from_spec(spec);spec.loader.exec_module(prior)
prepared=prior.prepared


def body_record(raw,truncated=False):
    return {'base64':base64.b64encode(raw).decode('ascii'),'bytes':len(raw),
            'sha256':c.digest(raw),'truncated':truncated}


def emulate_child(monkeypatch,body=b'{"reason":"synthetic missing run"}',*,read_fails=False,non_http=False,status=400):
    reads=[];calls=[];responses=[]
    class Body(io.BytesIO):
        def read(self,size=-1):
            reads.append(size)
            if read_fails:raise OSError('synthetic read failure')
            return super().read(size)
    def fetch(url,timeout,limit):
        calls.append((url,timeout,limit))
        if non_http:raise URLError('message=not-for-logs')
        response=Body(body);responses.append(response)
        raise HTTPError(url,status,'message=not-for-logs',{},response)
    monkeypatch.setattr(replay_p0,'_fetch',fetch)
    def run(command,**kwargs):
        assert command[:4]==[c.sys.executable,'-I','-B','-c']
        assert kwargs['timeout']==45 and set(kwargs['env'])=={'PATH','TZ'}
        out=io.BytesIO();err=io.StringIO()
        with monkeypatch.context() as m:
            m.setattr(c.sys,'argv',['child',*command[5:]])
            m.setattr(c.sys,'path',list(c.sys.path))
            m.setattr(c.sys,'stdout',SimpleNamespace(buffer=out))
            m.setattr(c.sys,'stderr',err)
            with pytest.raises(SystemExit) as exit:
                exec(command[4],{'__name__':'__main__'})
        assert exit.value.code==2
        return SimpleNamespace(returncode=2,stdout=out.getvalue(),stderr=err.getvalue().encode())
    monkeypatch.setattr(c.subprocess,'run',run)
    return reads,calls,responses


@pytest.mark.parametrize('body',[b'',b'{"reason":"synthetic missing run"}',b'\xff<script>never execute</script>',b'x'*1024,b'x'*1025,b'x'*5000])
def test_actual_child_program_captures_bounded_prefix_without_second_request(monkeypatch,body):
    reads,calls,responses=emulate_child(monkeypatch,body)
    with pytest.raises(c.TransportFailure) as caught:
        c.bounded_fetch('https://single-runs-api.open-meteo.com/v1/forecast',40,262144)
    e=caught.value
    assert e.code==400 and e.child_error_class=='HTTPError' and e.child_returncode==2
    assert e.http_error_body==body_record(body[:1024],len(body)>1024)
    assert len(calls)==1 and reads==[1025] and responses[0].closed
    assert 'not-for-logs' not in str(e) and '<script>' not in str(e)


@pytest.mark.parametrize('kind',['read_failure','non_http'])
def test_failed_body_read_or_non_http_error_keeps_safe_diagnostic(monkeypatch,kind):
    reads,calls,responses=emulate_child(monkeypatch,read_fails=kind=='read_failure',non_http=kind=='non_http')
    with pytest.raises(c.TransportFailure) as caught:c.bounded_fetch('unused',40,3)
    e=caught.value
    assert e.http_error_body is None and len(calls)==1
    if kind=='read_failure':assert e.code==400 and e.child_error_class=='HTTPError' and responses[0].closed
    else:assert e.code is None and e.child_error_class=='URLError' and not reads
    assert 'not-for-logs' not in str(e)


@pytest.mark.parametrize('key,value',[
    ('base64','%%%'),('base64','x'*2000),('base64',[]),('bytes',True),('bytes',1025),
    ('bytes',0),('sha256','0'*64),('truncated',1),('truncated',True),('extra','untrusted'),
])
def test_invalid_body_metadata_is_ignored_without_losing_http_status(monkeypatch,key,value):
    b=body_record(b'public error');b[key]=value
    stderr=json.dumps({'error_class':'HTTPError','code':400,'http_error_body':b}).encode()
    monkeypatch.setattr(c.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=2,stdout=b'',stderr=stderr))
    with pytest.raises(c.TransportFailure) as caught:c.bounded_fetch('unused',40,3)
    assert caught.value.code==400 and caught.value.http_error_body is None


@pytest.mark.parametrize('error_class,status',[('URLError',400),('HTTPError',302),('HTTPError',True),('HTTPError',200)])
def test_body_is_not_accepted_for_non_error_or_non_http_classes(error_class,status):
    e=c.TransportFailure(status,error_class=error_class,returncode=2,http_error_body=body_record(b'error'))
    assert e.http_error_body is None


def test_body_diagnostic_is_persisted_as_untrusted_base64_not_as_weather_and_pause_stays(prepared,monkeypatch):
    root,w,_=prepared
    reads,calls,_=emulate_child(monkeypatch)
    result=prior.step(w,fetch=c.bounded_fetch)
    assert result['status']=='paused' and result['reason']=='request_failed:TransportFailure:400'
    s=prior.load(root);b=s['failure_diagnostic']['http_error_body']
    assert b==body_record(b'{"reason":"synthetic missing run"}')
    assert s['inflight']['source']=={'kind':'reserved_http_attempt'}
    assert 'response_sha256' not in s['inflight'] and not list((root/'raw').iterdir())
    assert len(s['events'])==2 and not s['completed']
    assert prior.step(w,now=prior.NOW+timedelta(days=1),fetch=prior.forbidden)['status']=='paused'
    assert len(calls)==1 and len(prior.load(root)['events'])==2


def test_diagnostic_record_is_copied_not_aliased():
    b=body_record(b'error');e=c.TransportFailure(400,error_class='HTTPError',http_error_body=b)
    b['base64']='changed'
    assert e.http_error_body==body_record(b'error')


@pytest.mark.parametrize('status,captured',[(399,False),(400,True),(599,True),(600,False)])
def test_child_http_error_capture_status_boundaries(monkeypatch,status,captured):
    reads,calls,responses=emulate_child(monkeypatch,status=status)
    with pytest.raises(c.TransportFailure) as caught:c.bounded_fetch('unused',40,3)
    assert caught.value.code==(status if 100 <= status <= 599 else None) and len(calls)==1
    if captured:
        assert reads==[1025] and caught.value.http_error_body is not None and responses[0].closed
    else:
        assert not reads and caught.value.http_error_body is None
