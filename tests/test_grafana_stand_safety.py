"""Exercise the real stand CLI with recording stubs, never a Docker daemon.

The stand's initialized root lives under a temporary HOME. No user resources,
credentials, compose stacks, Docker containers or volumes are used by this suite.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
STAND = ROOT / "tests" / "grafana_stand"
FAKE_DOCKER = r'''
import json, os, re, sys
from pathlib import Path
a = sys.argv[1:]
with open(os.environ["DOCKER_CAPTURE"], "a") as log:
    safe = ["GF_SECURITY_ADMIN_PASSWORD=[disposable]" if x.startswith("GF_SECURITY_ADMIN_PASSWORD=") else x for x in a]
    log.write(json.dumps(safe) + "\n")
state = json.loads(Path(os.environ["DOCKER_STATE"]).read_text())
kind = a[0]
if a[:2] == ["context", "inspect"]:
    print(state.get("endpoint", "unix:///stub-only.sock"))
    sys.exit(0)
if kind in ("container", "volume", "network") and a[1] == "inspect":
    name = a[-1]
    rows = state.get(kind, {})
    if name not in rows:
        sys.exit(1)
    if "--format" in a:
        print(rows[name] or "<no value>")
    sys.exit(0)
if kind in ("container", "volume", "network") and a[1] == "ls":
    f = a[a.index("--filter") + 1]
    if f.startswith("name=^/"):
        name = f[len("name=^/"):-1]
        names = [name] if name in state.get(kind, {}) else []
    else:
        owner = f.split("=", 2)[-1]
        names = state.get("listed_" + kind, [n for n, label in state.get(kind, {}).items() if label == owner])
    print("\n".join(names))
    sys.exit(0)
if kind in ("volume", "network") and a[1] in ("create", "rm"):
    sys.exit(0)
if kind in ("run", "rm", "stop"):
    sys.exit(0)
raise SystemExit("unrecognized stub operation")
'''


@pytest.fixture
def stand(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "docker"
    stub.write_text(f"#!{sys.executable}\n" + FAKE_DOCKER)
    stub.chmod(0o700)
    capture = tmp_path / "docker.jsonl"
    statefile = tmp_path / "docker-state.json"
    statefile.write_text("{}")
    # Child processes and pytest failure reprs must not inherit API/SSH secrets.
    env = {"PATH": os.environ["PATH"], "LANG": "C.UTF-8"}
    env.update(HOME=str(home), GSTAND_WORK=str(home / ".sora-gstand" / "test-run"),
               DOCKER_CAPTURE=str(capture), DOCKER_STATE=str(statefile),
               PATH=str(bindir) + os.pathsep + os.environ["PATH"], PYTHONDONTWRITEBYTECODE="1")
    assert shutil.which("docker", path=env["PATH"]) == str(stub)

    def run(*args, ok=True):
        r = subprocess.run(["/bin/bash", str(STAND / "stand.sh"), *args],
                           env=env, capture_output=True, text=True, timeout=15)
        if ok:
            assert r.returncode == 0, r.stderr
        else:
            assert r.returncode != 0
        return r

    def calls():
        return [json.loads(line) for line in capture.read_text().splitlines()] if capture.exists() else []

    def state(**rows):
        statefile.write_text(json.dumps(rows))

    run("init")
    work = Path(env["GSTAND_WORK"])
    run_id = json.loads((work / "owner.json").read_text())["run_id"]
    return SimpleNamespace(run=run, env=env, work=work, owner=run_id,
                           prefix="sora-gstand-" + run_id, calls=calls, state=state, tmp=tmp_path)


def destructive(calls):
    return [a for a in calls if a[0] in {"rm", "stop"}
            or a[:2] in (["volume", "rm"], ["network", "rm"])]


def test_init_is_repeatable_but_does_not_reset_ownership(stand):
    before = (stand.work / "owner.json").read_bytes()
    stand.run("init")
    assert (stand.work / "owner.json").read_bytes() == before
    assert stand.calls() == []


@pytest.mark.parametrize("command", ["rm", "stop"])
@pytest.mark.parametrize("label", [None, "other-run"])
def test_even_matching_name_without_ownership_cannot_be_removed(stand, command, label):
    name = stand.prefix + "-a"
    stand.state(container={name: label})
    stand.run(command, "a", ok=False)
    assert destructive(stand.calls()) == []


@pytest.mark.parametrize("command", ["rm", "stop"])
def test_owned_operation_has_a_positive_control(stand, command):
    name = stand.prefix + "-a"
    stand.state(container={name: stand.owner})
    stand.run(command, "a")
    expected = ["rm", "-f", name] if command == "rm" else ["stop", name]
    assert destructive(stand.calls()) == [expected]


@pytest.mark.parametrize("argument", ["/", "../foreign", "-f", "a;echo unsafe", "sora-gstand-another-a"])
def test_foreign_or_invalid_resource_argument_never_deletes_foreign_resource(stand, argument):
    stand.run("rm", argument, ok=False)
    assert destructive(stand.calls()) == []


def test_original_foreign_container_reproducer_does_not_reach_deletion(stand):
    stand.state(container={"unrelated-user-container": None})
    stand.run("rm", "unrelated-user-container", ok=False)
    assert destructive(stand.calls()) == []
    assert all(a[-1] != "unrelated-user-container" for a in stand.calls())


@pytest.mark.parametrize("command", ["override", "provisioning-from-main"])
@pytest.mark.parametrize("argument", ["/", "../outside", "/tmp/foreign", "a/b"])
def test_output_path_arguments_are_rejected_without_deletion(stand, command, argument):
    sentinel = stand.tmp / "outside"
    sentinel.write_text("keep")
    stand.run(command, argument, ok=False)
    assert sentinel.read_text() == "keep"
    assert stand.calls() == []


@pytest.mark.parametrize("command", ["override", "provisioning-from-main"])
def test_existing_output_is_never_removed_or_overwritten(stand, command):
    out = stand.work / "outputs" / "keep"
    out.mkdir(parents=True)
    sentinel = out / "user-file"
    sentinel.write_text("keep")
    stand.run(command, "keep", ok=False)
    assert sentinel.read_text() == "keep"
    assert stand.calls() == []


@pytest.mark.parametrize("component", ["outputs", "leaf"])
def test_output_symlink_escape_is_refused(stand, component):
    outside = stand.tmp / "outside"
    outside.mkdir()
    (outside / "sentinel").write_text("keep")
    if component == "outputs":
        (stand.work / "outputs").symlink_to(outside, target_is_directory=True)
    else:
        (stand.work / "outputs").mkdir()
        (stand.work / "outputs" / "escape").symlink_to(outside, target_is_directory=True)
    stand.run("override", "escape", ok=False)
    assert list(outside.iterdir()) == [outside / "sentinel"]
    assert stand.calls() == []


def test_generated_override_has_only_the_documented_timing_changes(stand):
    r = stand.run("override", "fast")
    out = Path(r.stdout.strip())
    original = (ROOT / "grafana/provisioning/alerting/alerts.yml").read_text()
    expected = original.replace("    interval: 5m", "    interval: 30s").replace("for: 30m", "for: 1m").replace("for: 45m", "for: 2m").replace("for: 15m", "for: 1m")
    assert (out / "alerting/alerts.yml").read_text() == expected
    assert stand.calls() == []


def test_foreign_provisioning_is_refused_before_docker(stand):
    stand.run("grafana", "a", str(stand.tmp), ok=False)
    assert stand.calls() == []


def test_provisioning_symlink_is_refused_before_docker(stand):
    stand.run("override", "fast")
    prov = stand.work / "outputs/fast"
    (prov / "alerting/escape.yml").symlink_to(stand.tmp / "secret")
    stand.run("grafana", "a", str(prov), ok=False)
    assert stand.calls() == []


def test_grafana_refuses_an_unowned_named_volume_before_container_removal(stand):
    name = stand.prefix + "-a"
    stand.state(container={name: stand.owner}, volume={name + "-data": None})
    stand.run("grafana", "a", str(ROOT / "grafana/provisioning"), ok=False)
    assert destructive(stand.calls()) == []
    assert all(a[0] != "run" for a in stand.calls())


def test_new_containers_networks_and_volumes_are_labelled(stand):
    stand.run("up-base")
    stand.run("grafana", "a", str(ROOT / "grafana/provisioning"))
    creations = [a for a in stand.calls() if a[0] == "run" or a[:2] in (["network", "create"], ["volume", "create"])]
    assert len(creations) == 6
    for a in creations:
        assert a[a.index("--label") + 1] == "online.sora.gstand.run=" + stand.owner
    assert all(a[0] != "rm" for a in stand.calls())


def test_implicit_replacement_refuses_unowned_container(stand):
    stand.state(container={stand.prefix + "-metrics": "other-run"})
    stand.run("up-base", ok=False)
    assert destructive(stand.calls()) == []


def test_teardown_removes_only_this_runs_owned_resources(stand):
    own = stand.prefix + "-a"
    other = "sora-gstand-another-a"
    stand.state(container={own: stand.owner, other: "another"},
                volume={own + "-data": stand.owner, other + "-data": "another"},
                network={stand.prefix + "-net": stand.owner})
    stand.run("teardown")
    assert destructive(stand.calls()) == [["rm", "-f", own], ["volume", "rm", own + "-data"],
                                         ["network", "rm", stand.prefix + "-net"]]
    assert (stand.work / "owner.json").exists()


@pytest.mark.parametrize("kind", ["container", "volume", "network"])
def test_teardown_preflights_every_candidate_before_any_delete(stand, kind):
    own = stand.prefix + "-a"
    foreign = stand.prefix + "-foreign"
    state = {"container": {own: stand.owner}}
    state.setdefault(kind, {})[foreign] = "wrong-owner"
    state["listed_" + kind] = [own, foreign] if kind == "container" else [foreign]
    stand.state(**state)
    stand.run("teardown", ok=False)
    assert destructive(stand.calls()) == []


def test_even_forged_label_cannot_make_teardown_delete_a_foreign_prefix(stand):
    stand.state(container={"unrelated-user-container": stand.owner})
    stand.run("teardown", ok=False)
    assert destructive(stand.calls()) == []


@pytest.mark.parametrize("entry", ["owner.json", "admin-password", "targets"])
def test_known_run_entries_do_not_follow_symlinks(stand, entry):
    outside = stand.tmp / "outside"
    outside.write_text("keep")
    path = stand.work / entry
    if path.exists():
        path.unlink()  # Only this test's own generated marker.
    path.symlink_to(outside)
    if entry == "admin-password":
        stand.run("grafana", "a", str(ROOT / "grafana/provisioning"), ok=False)
    elif entry == "targets":
        stand.run("targets", "restore", ok=False)
    else:
        stand.run("names", ok=False)
    assert outside.read_text() == "keep"
    assert stand.calls() == []


def test_targets_match_this_runs_metrics_name_and_can_vanish(stand):
    stand.run("targets", "restore")
    path = stand.work / "targets/sora-app.json"
    assert json.loads(path.read_text()) == [{"targets": [stand.prefix + "-metrics:8000"]}]
    stand.run("targets", "vanish")
    assert json.loads(path.read_text()) == []
    assert stand.calls() == []


def test_read_only_probe_never_generates_a_missing_password(stand, monkeypatch):
    monkeypatch.setenv("HOME", stand.env["HOME"])
    monkeypatch.setenv("GSTAND_WORK", stand.env["GSTAND_WORK"])
    monkeypatch.syspath_prepend(str(STAND))
    spec = importlib.util.spec_from_file_location("stand_check_for_test", STAND / "check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(FileNotFoundError):
        module._password()
    assert not (stand.work / "admin-password").exists()
    value = "a" * 64
    (stand.work / "admin-password").write_text(value + "\n")
    assert module._password() == value


def test_work_root_cannot_adopt_user_files(stand):
    work = Path(stand.env["HOME"]) / ".sora-gstand/other"
    work.mkdir()
    (work / "keep").write_text("keep")
    stand.env["GSTAND_WORK"] = str(work)
    stand.run("init", ok=False)
    assert (work / "keep").read_text() == "keep"
    assert not (work / "owner.json").exists()


@pytest.mark.parametrize("work", ["/", "../relative", "outside"])
def test_work_root_must_be_beneath_dedicated_root(stand, work):
    stand.env["GSTAND_WORK"] = str(stand.tmp / work) if work == "outside" else work
    stand.run("init", ok=False)
    assert stand.calls() == []


@pytest.mark.parametrize("endpoint", ["ssh://remote", "tcp://remote:2376", "http://remote"])
def test_remote_docker_host_is_refused_before_any_docker_invocation(stand, endpoint):
    stand.env["DOCKER_HOST"] = endpoint
    stand.run("teardown", ok=False)
    assert stand.calls() == []


def test_remote_context_is_inspected_locally_but_never_contacted(stand):
    stand.state(endpoint="ssh://remote")
    stand.env["DOCKER_CONTEXT"] = "remote-context"
    stand.run("teardown", ok=False)
    assert stand.calls()
    assert all(a[:2] == ["context", "inspect"] for a in stand.calls())


def test_a_symlinked_work_root_cannot_adopt_the_target(stand):
    outside = stand.tmp / "outside"
    outside.mkdir()
    link = stand.work.parent / "escape"
    link.symlink_to(outside, target_is_directory=True)
    stand.env["GSTAND_WORK"] = str(link)
    stand.run("init", ok=False)
    assert list(outside.iterdir()) == []


def test_a_writable_shared_root_is_refused(stand):
    stand.work.chmod(0o777)
    stand.run("names", ok=False)
    assert stand.calls() == []


def test_a_hardlinked_marker_is_refused(stand):
    os.link(stand.work / "owner.json", stand.tmp / "copy")
    stand.run("names", ok=False)
    assert stand.calls() == []


@pytest.mark.parametrize("marker", ["[]", "{}", '{"version": 1, "run_id": "bad"}', "not json"])
def test_corrupt_markers_fail_without_operations(stand, marker):
    (stand.work / "owner.json").write_text(marker)
    stand.run("teardown", ok=False)
    assert stand.calls() == []
