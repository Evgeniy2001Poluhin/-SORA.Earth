"""Offline controls/timeline tests: no daemon, stand password or real waits."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

HERE = Path(__file__).resolve().parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


driver = load("scenario_c", HERE / "grafana_stand/scenario_c.py")
control = load("control", HERE / "grafana_stand/control.py")
safety = load("stand_safety", HERE / "test_grafana_stand_safety.py")
stand = safety.stand


class Clock:
    def __init__(self):
        self.now = 0

    def read(self):
        return self.now

    def sleep(self, seconds):
        assert 0 <= seconds <= 10
        self.now += seconds


def test_fixed_timeline_and_restoration_with_no_real_wait():
    clock = Clock()
    actions, rows = [], []
    driver.run(lambda cmd: actions.append((clock.now, cmd)), lambda: [], rows.append,
               clock=clock.read, wall=clock.read, sleep=clock.sleep)
    assert [(t, list(cmd)) for t, cmd in actions[:5]] == [
        (e["at_seconds"], list(e["command"])) for e in driver.plan()["events"]]
    assert [t for t, _ in actions[:5]] == [0, 900, 3600, 5100, 5100]
    assert clock.now == 5700
    assert actions[-2:][0][1] == ("targets", "restore")
    assert actions[-1][1] == ("control", *driver.BASELINE)
    assert rows[-1]["kind"] == "finished"


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_observer_failure_or_interrupt_restores_both_controls(failure):
    actions, rows = [], []
    def fail():
        raise failure("secret not to log")
    with pytest.raises(failure):
        driver.run(actions.append, fail, rows.append)
    assert actions[-2:] == [("targets", "restore"), ("control", *driver.BASELINE)]
    assert rows[-3]["kind"] == "aborted"
    assert "secret" not in json.dumps(rows)
    assert not any(r["kind"] == "finished" for r in rows)


def test_restore_failure_is_recorded_and_second_restore_still_attempted():
    actions, rows = [], []
    def perform(cmd):
        actions.append(cmd)
        if cmd == ("targets", "restore"):
            raise OSError("not logged")
    def fail():
        raise ValueError("observer unavailable")
    with pytest.raises(RuntimeError, match="restoration incomplete"):
        driver.run(perform, fail, rows.append)
    assert actions[-1] == ("control", *driver.BASELINE)
    assert [r["ok"] for r in rows if r["kind"] == "restoration"] == [False, True]


@pytest.mark.parametrize("wall_jump", [False, True])
def test_sleep_or_clock_jump_aborts_instead_of_compressing_timeline(wall_jump):
    clock = Clock()
    actions, rows = [], []
    def sleep(_):
        clock.now += 1000
    wall = (lambda: clock.now * 2) if wall_jump else clock.read
    with pytest.raises(RuntimeError):
        driver.run(actions.append, lambda: [], rows.append,
                   clock=clock.read, wall=wall, sleep=sleep)
    assert len(actions) == 3  # initial condition plus both restorations
    assert not any(r["kind"] == "finished" for r in rows)


@pytest.mark.parametrize("args", [[], ["unknown=1"], ["app=bad"], ["lat_ms=nan"],
    ["lat_ms=inf"], ["lat_n=1.2"], ["drift_on=2"], ["gap=-1"],
    ["app=ok", "app=down"], ["ts_lag=1&app=down"], ["retrain_since=-2"]])
def test_invalid_controls_are_rejected_before_docker(stand, args):
    with pytest.raises(ValueError):
        control.query(args)
    stand.run("control", *args, ok=False)
    assert stand.calls() == []


@pytest.mark.parametrize("owner", [None, "foreign"])
def test_valid_control_never_executes_in_foreign_container(stand, owner):
    stand.state(container={stand.prefix + "-metrics": owner})
    stand.run("control", "gap=3", ok=False)
    assert not any(c[0] == "exec" for c in stand.calls())


def test_control_positive_uses_only_owned_container_and_fixed_local_url(stand):
    # Extend the recording stub, never invoke Docker or even an HTTP endpoint.
    stub = Path(stand.env["PATH"].split(":")[0]) / "docker"
    stub.write_text(stub.read_text().replace('("run", "rm", "stop")', '("run", "rm", "stop", "exec")'))
    stand.state(container={stand.prefix + "-metrics": stand.owner})
    stand.run("control", "gap=3", "lat_ms=900")
    calls = [c for c in stand.calls() if c[0] == "exec"]
    assert len(calls) == 1
    assert calls[0][1:5] == [stand.prefix + "-metrics", "python", "-I", "-c"]
    assert 'http://127.0.0.1:8000/control?' in calls[0][5]
    assert calls[0][-1] == "gap=3&lat_ms=900"


def test_sanitized_states_do_not_copy_error_text_or_labels():
    row = {"uid": "sora-app-down", "state": "firing", "health": "error",
           "lastError": "token=private", "labels": {"password": "private"},
           "alerts": [{"state": "Alerting", "labels": {"api_key": "private"}}]}
    out = driver.sanitized_states([row])
    assert out[0]["has_error"] is True
    assert "private" not in json.dumps(out)
    with pytest.raises(ValueError):
        driver.sanitized_states([])
    with pytest.raises(ValueError):
        driver.sanitized_states([dict(row, uid="token=private")])


def test_plan_default_is_inert_even_without_initialized_stand(tmp_path):
    result = subprocess.run([sys.executable, "-B", str(HERE / "grafana_stand/scenario_c.py")],
                            cwd=tmp_path, env={"HOME": str(tmp_path)},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["schema"] == "local-stand-scenario-c-v1"
    assert list(tmp_path.iterdir()) == []


def test_driver_manifest_capture_and_no_overwrite_without_real_services(stand, monkeypatch):
    guard = load("path_guard", HERE / "grafana_stand/path_guard.py")
    probe = load("check", HERE / "grafana_stand/check.py")
    monkeypatch.setitem(sys.modules, "path_guard", guard)
    monkeypatch.setitem(sys.modules, "check", probe)
    monkeypatch.setenv("HOME", stand.env["HOME"])
    monkeypatch.setenv("GSTAND_WORK", stand.env["GSTAND_WORK"])
    actions = []
    monkeypatch.setattr(driver, "command", actions.append)
    monkeypatch.setattr(probe, "states", lambda: [
        {"uid": "sora-app-down", "state": "inactive", "health": "ok", "alerts": []}])
    real_run = driver.run
    clock = Clock()
    monkeypatch.setattr(driver, "run", lambda perform, observe, emit:
        real_run(perform, observe, emit, clock=clock.read, wall=clock.read, sleep=clock.sleep))
    monkeypatch.setattr(sys, "argv", ["scenario_c.py", "--execute", "capture"])
    driver.main()
    out = stand.work / "outputs/capture"
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["run_id"] == stand.owner
    assert manifest["plan"]["duration_seconds"] == 5700
    assert "tests/grafana_stand/stand-prometheus.yml" in manifest["sha256"]
    timeline = (out / "timeline.jsonl").read_bytes()
    rows = [json.loads(line) for line in timeline.splitlines()]
    assert rows[-1]["kind"] == "finished"
    assert all(r["observed_at_utc"].endswith("+00:00") for r in rows)
    assert actions[0] == ("verify", "c")
    with pytest.raises(FileExistsError):
        driver.main()
    assert (out / "timeline.jsonl").read_bytes() == timeline
    assert stand.calls() == []
    assert not (stand.work / "admin-password").exists()


def test_verify_refuses_missing_owned_grafana(stand):
    stand.state(container={stand.prefix + "-metrics": stand.owner,
                           stand.prefix + "-prometheus": stand.owner})
    stand.run("verify", "c", ok=False)
    assert not safety.destructive(stand.calls())


def test_persistent_log_failure_never_skips_either_restoration():
    actions = []
    def fail_log(_row):
        raise OSError("disk full")
    with pytest.raises(OSError):
        driver.run(actions.append, lambda: [], fail_log)
    assert actions[-2:] == [("targets", "restore"), ("control", *driver.BASELINE)]


@pytest.mark.parametrize("operation", ["observe", "perform"])
def test_time_jump_inside_blocking_operation_cannot_finish_successfully(operation):
    clock = Clock()
    rows, actions = [], []
    def perform(cmd):
        actions.append(cmd)
        if operation == "perform":
            clock.now += 6000
    def observe():
        if operation == "observe":
            clock.now += 6000
        return []
    with pytest.raises(RuntimeError):
        driver.run(perform, observe, rows.append,
                   clock=clock.read, wall=clock.read, sleep=clock.sleep)
    assert actions[-2:] == [("targets", "restore"), ("control", *driver.BASELINE)]
    assert not any(r["kind"] == "finished" for r in rows)
