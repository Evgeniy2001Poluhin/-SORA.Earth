"""Fake transport/clock and tmp_path only. No public API or project artifacts."""
from datetime import datetime, timedelta, timezone
import fcntl
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from app.services.forecasting import replay_cache_worker as cache

NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
CODE = "a" * 40


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    plan = cache.build_plan()
    plan["requests"] = plan["requests"][:3]
    plan["reuse_candidates"] = []
    plan["probe_requests"] = []
    monkeypatch.setattr(cache, "build_plan", lambda: plan)
    root = tmp_path / "cache"
    raw = cache.canonical(plan)
    cache.initialize(root, raw, cache.digest(raw), prior_units=0, now=NOW, code_commit=CODE)
    worker = cache.CacheWorker(root)
    # Mechanical persistence/quota tests use a tiny synthetic byte contract.
    def validate(raw, request):
        cache.need(raw == b"synthetic-good", "synthetic validation failed")
    monkeypatch.setattr(worker, "validate", validate)
    return root, worker, plan


def load(root):
    return json.loads((root / "state.json").read_bytes())


def edit(root, change):
    state = load(root)
    change(state)
    cache.save_state(root, state)


def step(worker, now=NOW, fetch=lambda *args: b"synthetic-good", code=CODE):
    return worker.step(now=now, fetch=fetch, code_commit=code)


def forbidden(*args):
    pytest.fail("unexpected transport call")


def test_one_step_reserves_on_disk_before_fetch_and_resume_skips_completed(prepared):
    root, worker, plan = prepared
    calls = []
    def transport(url, timeout, limit):
        state = load(root)
        assert state["inflight"]["day"] == plan["requests"][0]["issue_date"]
        assert state["events"][-1]["weight"] == 21
        assert len(state["events"]) == 2
        calls.append(url)
        return b"synthetic-good"
    assert step(worker, fetch=transport)["status"] == "downloaded"
    assert len(calls) == 1
    # New instance resumes disk state; start spacing applies, no HTTP.
    restarted = cache.CacheWorker(root)
    restarted.validate = worker.validate
    assert step(restarted, now=NOW+timedelta(seconds=3), fetch=forbidden)["status"] == "quota_wait"
    result = step(restarted, now=NOW+timedelta(seconds=4))
    assert result["day"] == plan["requests"][1]["issue_date"]
    assert len(load(root)["completed"]) == 2


@pytest.mark.parametrize("weight,seconds", [(400,60),(4000,3600),(7000,86400)])
def test_quota_blocks_until_exact_rolling_expiry(prepared, weight, seconds):
    root, worker, _ = prepared
    edit(root, lambda s: s["events"][0].update(weight=weight))
    before = step(worker, now=NOW+timedelta(seconds=seconds-1), fetch=forbidden)
    assert before == {"status": "quota_wait", "not_before": (NOW+timedelta(seconds=seconds)).isoformat()}
    assert step(worker, now=NOW+timedelta(seconds=seconds))["status"] == "downloaded"


def test_calendar_midnight_does_not_reset_rolling_day(prepared):
    root, worker, _ = prepared
    edit(root, lambda s: s["events"][0].update(weight=7000))
    midnight=NOW.replace(hour=0)+timedelta(days=1)
    assert step(worker, now=midnight, fetch=forbidden)["status"] == "quota_wait"


def test_real_clock_accounts_until_transport_completion(prepared):
    root,worker,_=prepared
    clock=iter([NOW,NOW+timedelta(seconds=30)])
    assert worker.step(now=NOW,fetch=lambda *args:b"synthetic-good",code_commit=CODE,
                       clock=lambda:next(clock))["status"]=="downloaded"
    event=load(root)["events"][-1]
    assert event["reserved_at"]==NOW.isoformat()
    assert event["at"]==(NOW+timedelta(seconds=30)).isoformat()
    result=step(worker,now=NOW+timedelta(seconds=31),fetch=forbidden)
    assert result=={"status":"quota_wait","not_before":(NOW+timedelta(seconds=34)).isoformat()}


def test_reservation_write_failure_never_calls_transport(prepared, monkeypatch):
    _, worker, _ = prepared
    def failed_save(*args): raise OSError("disk failure")
    monkeypatch.setattr(cache, "save_state", failed_save)
    with pytest.raises(OSError): step(worker, fetch=forbidden)


@pytest.mark.parametrize("kind", ["429", "timeout", "malformed", "oversize"])
def test_failed_attempt_is_spent_and_globally_pauses_without_retry(prepared, kind):
    root, worker, _ = prepared
    def bad(*args):
        if kind == "429": raise HTTPError("https://example.invalid",429,"limited",{},None)
        if kind == "timeout": raise TimeoutError()
        if kind == "oversize": return b"x"*(cache.MAX_RAW+1)
        return b"invalid"
    result = step(worker, fetch=bad)
    assert result["status"] == "paused"
    if kind == "429": assert result["reason"].endswith(":429")
    state=load(root)
    assert state["events"][-1]["weight"] == 21
    assert state["inflight"]
    assert step(worker, now=NOW+timedelta(days=2), fetch=forbidden)["status"] == "paused"
    assert len(load(root)["events"]) == 2


@pytest.mark.parametrize("raw_saved", [False, True])
def test_crash_keeps_reservation_and_recovers_only_saved_bytes(prepared, raw_saved):
    root, worker, plan = prepared
    day=plan["requests"][0]["issue_date"]
    original_complete = worker.complete
    def crash(*args):
        raise KeyboardInterrupt()
    def crash_after_write(*args, **kwargs): raise KeyboardInterrupt()
    if raw_saved: worker.complete = crash_after_write
    with pytest.raises(KeyboardInterrupt):
        step(worker, fetch=(lambda *args:b"synthetic-good") if raw_saved else crash)
    worker.complete = original_complete
    assert load(root)["inflight"]["day"] == day
    result=step(worker,now=NOW+timedelta(seconds=5),fetch=forbidden)
    assert result["status"] == ("recovered_without_http" if raw_saved else "paused")
    assert len(load(root)["events"]) == 2
    if raw_saved: assert load(root)["completed"][day]["source"]["recovered_without_http"]


def test_recovery_checks_previously_persisted_raw_digest(prepared,monkeypatch):
    root,worker,plan=prepared
    complete=worker.complete
    def crash(*args,**kwargs):raise KeyboardInterrupt()
    monkeypatch.setattr(worker,"complete",crash)
    with pytest.raises(KeyboardInterrupt):step(worker)
    monkeypatch.setattr(worker,"complete",complete)
    monkeypatch.setattr(worker,"validate",lambda *args:None)
    (root/"raw"/plan["requests"][0]["file"]).write_bytes(b"synthetic-tamper")
    result=step(worker,now=NOW+timedelta(seconds=5),fetch=forbidden)
    assert result["status"]=="paused" and result["reason"]=="recovery_failed:CacheRefusal"
    assert not load(root)["completed"]


def test_lock_contention_refuses_before_fetch(prepared):
    root, worker, _ = prepared
    with (root/"worker.lock").open("r+b") as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(cache.CacheRefusal,match="lock busy"): step(worker,fetch=forbidden)
    assert len(load(root)["events"]) == 1


def test_clock_rollback_and_code_mismatch_refuse(prepared):
    _, worker, _ = prepared
    with pytest.raises(cache.CacheRefusal,match="rollback"):
        step(worker,now=NOW-timedelta(seconds=1),fetch=forbidden)
    with pytest.raises(cache.CacheRefusal,match="code differs"):
        step(worker,fetch=forbidden,code="b"*40)


def test_completed_bytes_cannot_be_silently_replaced(prepared):
    root, worker, plan = prepared
    step(worker)
    (root/"raw"/plan["requests"][0]["file"]).write_bytes(b"tampered")
    with pytest.raises(cache.CacheRefusal,match="pin mismatch"):
        step(worker,now=NOW+timedelta(seconds=5),fetch=forbidden)


def test_deleted_reservation_cannot_be_hidden_behind_completed_receipt(prepared):
    root, worker, _ = prepared
    step(worker)
    edit(root,lambda s: s["events"].pop())
    with pytest.raises(cache.CacheRefusal,match="missing completed quota"):
        step(worker,now=NOW+timedelta(seconds=5),fetch=forbidden)


def test_source_import_pin_and_idempotency_use_no_http(prepared, tmp_path):
    root, worker, plan=prepared;request=plan["requests"][0]
    source=tmp_path/"source";source.mkdir()
    raw=b"synthetic-good";cache.write_new(source/"forecast-00.json",raw)
    manifest={"complete":True,"entries":[{**request,"file":"forecast-00.json","sha256":cache.digest(raw),"bytes":len(raw)}]}
    data=cache.canonical(manifest);cache.write_new(source/"manifest.json",data)
    with pytest.raises(cache.CacheRefusal,match="manifest pin mismatch"):
        worker.import_one(request["issue_date"],source,"0"*64,now=NOW)
    assert worker.import_one(request["issue_date"],source,cache.digest(data),now=NOW)["status"]=="imported"
    assert worker.import_one(request["issue_date"],source,cache.digest(data),now=NOW)["status"]=="already_present"
    assert len(load(root)["events"])==1


@pytest.mark.parametrize("defect",["traversal","symlink","hash","payload"])
def test_invalid_import_never_mutates_completion_or_spends_quota(prepared,tmp_path,defect):
    root,worker,plan=prepared;request=plan["requests"][0]
    source=tmp_path/"bad-source";source.mkdir()
    raw=b"invalid" if defect=="payload" else b"synthetic-good"
    name="../outside.json" if defect=="traversal" else "forecast-00.json"
    if defect=="symlink":
        external=tmp_path/"external.json";external.write_bytes(raw)
        (source/name).symlink_to(external)
    elif defect!="traversal":(source/name).write_bytes(raw)
    entry={**request,"file":name,"sha256":"0"*64 if defect=="hash" else cache.digest(raw),"bytes":len(raw)}
    data=cache.canonical({"complete":True,"entries":[entry]});cache.write_new(source/"manifest.json",data)
    with pytest.raises(ValueError):worker.import_one(request["issue_date"],source,cache.digest(data),now=NOW)
    assert not load(root)["completed"] and len(load(root)["events"])==1


def test_import_crash_recovers_saved_pinned_bytes_without_http(prepared,tmp_path,monkeypatch):
    root,worker,plan=prepared;request=plan["requests"][0]
    source=tmp_path/"import-source";source.mkdir();raw=b"synthetic-good"
    cache.write_new(source/"forecast.json",raw)
    data=cache.canonical({"complete":True,"entries":[{**request,"file":"forecast.json","sha256":cache.digest(raw),"bytes":len(raw)}]})
    cache.write_new(source/"manifest.json",data)
    complete=worker.complete
    def crash(*args,**kwargs):raise KeyboardInterrupt()
    monkeypatch.setattr(worker,"complete",crash)
    with pytest.raises(KeyboardInterrupt):worker.import_one(request["issue_date"],source,cache.digest(data),now=NOW)
    monkeypatch.setattr(worker,"complete",complete)
    assert step(worker,fetch=forbidden)["status"]=="recovered_without_http"
    receipt=load(root)["completed"][request["issue_date"]]
    assert receipt["source"]["kind"]=="import"
    assert receipt["source"]["manifest_sha256"]==cache.digest(data)
    assert len(load(root)["events"])==1


def test_required_existing_sources_are_not_redownloaded(prepared):
    _,worker,plan=prepared
    plan["probe_requests"]=[plan["requests"][0]]
    # Re-pin this synthetic test plan; operational plans cannot mutate this way.
    cache.write_new(worker.root/"test-plan.json",cache.canonical(plan))
    (worker.root/"plan.json").write_bytes(cache.canonical(plan))
    edit(worker.root,lambda s:s.update(plan_sha256=cache.digest(cache.canonical(plan))))
    assert step(worker,fetch=forbidden)["status"]=="imports_required"


def test_root_and_raw_symlinks_refuse_without_touching_targets(prepared,tmp_path):
    root,worker,_=prepared
    link=tmp_path/"link";link.symlink_to(root,target_is_directory=True)
    with pytest.raises(cache.CacheRefusal): step(cache.CacheWorker(link),fetch=forbidden)
    (root/"raw").rename(root/"saved-raw")
    (root/"raw").symlink_to(root/"saved-raw",target_is_directory=True)
    with pytest.raises(cache.CacheRefusal): step(worker,fetch=forbidden)


def test_initialization_never_overwrites_existing_ledger(prepared):
    root,_,plan=prepared;before=(root/"state.json").read_bytes()
    raw=cache.canonical(plan)
    with pytest.raises(cache.CacheRefusal,match="new cache"):
        cache.initialize(root,raw,cache.digest(raw),prior_units=0,now=NOW,code_commit=CODE)
    assert (root/"state.json").read_bytes()==before


def test_successful_fake_run_stops_at_complete_grid_and_audits(prepared):
    _,worker,_=prepared
    for second in (0,4,8):assert step(worker,now=NOW+timedelta(seconds=second))["status"]=="downloaded"
    assert step(worker,now=NOW+timedelta(seconds=12),fetch=forbidden)["status"]=="all_downloaded_pending_final_audit"
    assert worker.audit(now=NOW+timedelta(seconds=12))["ready_for_review"]


def test_real_parser_accepts_origin_day_and_rejects_missing_target(prepared):
    _,worker,plan=prepared;request=plan["requests"][0]
    start=datetime.fromisoformat(request["origin"]).replace(hour=0,minute=0,second=0)
    times=[(start+timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(192)]
    payload=[{"latitude":p.lat,"longitude":p.lon,"timezone":"UTC","utc_offset_seconds":0,
              "hourly_units":{"time":"iso8601","temperature_2m":"°C"},
              "hourly":{"time":times.copy(),"temperature_2m":[10.0]*192}}
             for p in cache.OPENMETEO_POINTS_V1]
    cache.CacheWorker.validate(worker,cache.canonical(payload),request)
    payload[0]["hourly"]["temperature_2m"][24]=None
    with pytest.raises(ValueError):cache.CacheWorker.validate(worker,cache.canonical(payload),request)


def test_cli_status_is_offline_and_fetch_code_confirmation_refuses(prepared,monkeypatch,capsys):
    root,_,_=prepared
    script=Path(__file__).parents[1]/"scripts/forecast_replay_cache_worker.py"
    spec=importlib.util.spec_from_file_location("cache_worker_cli_test",script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    class FixedClock:
        @staticmethod
        def now(zone):return NOW
    monkeypatch.setattr(module,"datetime",FixedClock)
    monkeypatch.setattr(module,"CacheWorker",lambda _:prepared[1])
    assert module.main(["status",str(root)])==0
    assert json.loads(capsys.readouterr().out)["network_requests"]==0
    monkeypatch.setattr(module,"frozen_head",lambda:CODE)
    assert module.main(["fetch-one",str(root),"--confirm-code-commit","b"*40])==2


def test_cli_init_is_offline_and_refuses_existing_directory(prepared,tmp_path,monkeypatch,capsys):
    _,_,plan=prepared
    script=Path(__file__).parents[1]/"scripts/forecast_replay_cache_worker.py"
    spec=importlib.util.spec_from_file_location("cache_worker_cli_init_test",script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,"frozen_head",lambda:CODE)
    raw=cache.canonical(plan);file=tmp_path/"plan.json";file.write_bytes(raw)
    args=["init",str(tmp_path/"cli-cache"),"--plan",str(file),"--plan-sha256",cache.digest(raw),"--prior-reserved-units","2472"]
    assert module.main(args)==0
    assert json.loads(capsys.readouterr().out)["network_requests"]==0
    assert module.main(args)==2


@pytest.mark.parametrize("mode",["success","deadline","429","oversize"])
def test_transport_subprocess_is_bounded_and_sanitized_without_network(monkeypatch,mode):
    def run(command,**kwargs):
        assert command[:4]==[cache.sys.executable,"-I","-B","-c"]
        assert kwargs["timeout"]==45 and set(kwargs["env"])=={"PATH","TZ"}
        if mode=="deadline":raise subprocess.TimeoutExpired(command,45)
        if mode=="429":return SimpleNamespace(returncode=2,stdout=b"",stderr=b'{"error_class":"HTTPError","code":429}')
        return SimpleNamespace(returncode=0,stdout=b"x"*(4 if mode=="oversize" else 3),stderr=b"")
    monkeypatch.setattr(cache.subprocess,"run",run)
    if mode=="success":assert cache.bounded_fetch("https://single-runs-api.open-meteo.com/v1/forecast",40,3)==b"xxx"
    elif mode=="deadline":
        with pytest.raises(TimeoutError):cache.bounded_fetch("unused",40,3)
    elif mode=="429":
        with pytest.raises(cache.TransportFailure) as exc:cache.bounded_fetch("unused",40,3)
        assert exc.value.code==429
    else:
        with pytest.raises(cache.CacheRefusal,match="byte limit"):cache.bounded_fetch("unused",40,3)
