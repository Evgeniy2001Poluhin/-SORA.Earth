"""`/metrics` must report every worker, not whichever one answered (#262).

The backend runs `gunicorn -w 4`, and `prometheus_client` keeps each metric in
the memory of the process that touched it. Measured on production 2026-09-06:
five scrapes landed on two different workers, one reporting two
`sora_telemetry_tasks_total` series and the other reporting none, for an event
that had definitely happened.

The error is **not** "about four times low". Gunicorn does not distribute
requests round-robin, so a counter's scraped value can be anywhere between its
full value and zero, and it moves between scrapes as a different worker
answers. That is worse than a constant factor: it cannot be corrected for, and
a series that jitters looks like activity.

These tests use real subprocesses. A thread would share the process's memory
and pass whatever the code did.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run(code: str, mp_dir: Path, expect_ok: bool = True) -> str:
    """Run `code` in a fresh process sharing the multiprocess directory."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": "/tmp",
        "PROMETHEUS_MULTIPROC_DIR": str(mp_dir),
    }
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120, env=env,
    )
    if expect_ok:
        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr[-2000:]}"
    return result.stdout


@pytest.fixture()
def mp_dir(tmp_path):
    d = tmp_path / "prom"
    d.mkdir()
    return d


# --- the property the issue is about ---------------------------------------


def test_one_scrape_sees_every_process(mp_dir):
    """Point 8: two processes increment; one scrape reports the sum."""
    for value in (3, 4):
        _run(f"""
            from prometheus_client import Counter, CollectorRegistry
            c = Counter("probe_events_total", "d", registry=CollectorRegistry())
            c.inc({value})
        """, mp_dir)

    out = _run("""
        from prometheus_client import CollectorRegistry, generate_latest, multiprocess
        r = CollectorRegistry(); multiprocess.MultiProcessCollector(r)
        for line in generate_latest(r).decode().splitlines():
            if line.startswith("probe_events_total"):
                print(line)
    """, mp_dir)

    assert "probe_events_total 7.0" in out, out


def test_without_the_directory_a_scrape_sees_only_its_own_process(tmp_path):
    """The negative control, and the defect itself.

    Without this the test above proves only that addition works. Here two
    processes increment with no shared directory and the third sees nothing --
    which is exactly what production was doing.
    """
    lonely = tmp_path / "unused"
    lonely.mkdir()
    for value in (3, 4):
        subprocess.run(
            [sys.executable, "-c", textwrap.dedent("""
                from prometheus_client import Counter, REGISTRY
                Counter("probe_events_total", "d").inc(%d)
            """ % value)],
            cwd=str(ROOT), capture_output=True, text=True, timeout=60,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/tmp"},
        )
    out = subprocess.run(
        [sys.executable, "-c", textwrap.dedent("""
            from prometheus_client import REGISTRY, generate_latest
            print([l for l in generate_latest(REGISTRY).decode().splitlines()
                   if l.startswith("probe_events_total")])
        """)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/tmp"},
    ).stdout

    assert "probe_events_total" not in out or "7.0" not in out, (
        "the isolated scrape saw another process's counter, so the setup proves nothing"
    )


def test_a_finished_process_does_not_take_its_counter_with_it(mp_dir):
    """Point 8: a worker exiting must not undo the work it recorded."""
    _run("""
        from prometheus_client import Counter, CollectorRegistry
        Counter("probe_events_total", "d", registry=CollectorRegistry()).inc(5)
    """, mp_dir)

    out = _run("""
        from prometheus_client import CollectorRegistry, generate_latest, multiprocess
        r = CollectorRegistry(); multiprocess.MultiProcessCollector(r)
        print([l for l in generate_latest(r).decode().splitlines()
               if l.startswith("probe_events_total")])
    """, mp_dir)

    assert "5.0" in out, out


def test_mark_process_dead_reaps_only_live_mode_gauges(mp_dir):
    """Measured, because I had assumed otherwise and wrote a failing test.

    `mark_process_dead` deletes `gauge_live*_<pid>.db` and nothing else. The
    non-live modes -- `all`, `max`, `min`, `sum`, `mostrecent` -- are *meant*
    to outlive the process that wrote them, and counters always are: removing a
    departed worker's counter would make a recycle look like a monotonic series
    going backwards.

    Every gauge in `app/prom_metrics.py` uses `mostrecent`, and that is the
    point of choosing it: a worker recycle must not erase the last known value
    of the model's state until something recomputes it.
    """
    pid = _run("""
        import os
        from prometheus_client import Counter, Gauge, CollectorRegistry
        reg = CollectorRegistry()
        Counter("probe_events_total", "d", registry=reg).inc(9)
        Gauge("probe_state", "d", registry=reg, multiprocess_mode="mostrecent").set(1)
        Gauge("probe_live_state", "d", registry=reg, multiprocess_mode="livemostrecent").set(1)
        print(os.getpid())
    """, mp_dir).strip()

    scrape = """
        from prometheus_client import CollectorRegistry, generate_latest, multiprocess
        r = CollectorRegistry(); multiprocess.MultiProcessCollector(r)
        print([l for l in generate_latest(r).decode().splitlines() if l.startswith("probe_")])
    """
    before = _run(scrape, mp_dir)
    assert "probe_state 1.0" in before and "probe_live_state 1.0" in before, before

    _run(f"""
        from prometheus_client import multiprocess
        multiprocess.mark_process_dead({pid})
    """, mp_dir)
    after = _run(scrape, mp_dir)

    assert "probe_live_state" not in after, f"a live-mode gauge survived: {after}"
    assert "probe_state 1.0" in after, f"a non-live gauge was reaped: {after}"
    assert "probe_events_total 9.0" in after, f"the counter was reaped: {after}"


def test_the_child_exit_hook_actually_calls_mark_process_dead(monkeypatch, mp_dir):
    """The hook reaps nothing today, and is wired anyway -- so it is tested.

    No gauge in this application uses a live mode, so `mark_process_dead` has
    no file to delete right now. That makes the hook the kind of mechanism this
    repository distrusts: present, plausible, and doing nothing observable. It
    stays because the day someone picks `livesum` for a "requests in flight"
    gauge, its absence means a dead worker's value is served forever -- and
    that day nobody will be reading this file. What is not acceptable is
    keeping it *untested*, so this runs it.
    """
    import importlib

    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(mp_dir))
    conf = importlib.import_module("gunicorn_conf")

    called = []
    from prometheus_client import multiprocess

    monkeypatch.setattr(multiprocess, "mark_process_dead", lambda pid: called.append(pid))

    class _Worker:
        pid = 4242

    class _Server:
        class log:
            @staticmethod
            def warning(*a, **k):
                pass

    conf.child_exit(_Server(), _Worker())

    assert called == [4242]


def test_the_hook_does_nothing_when_multiprocess_is_off(monkeypatch):
    """A single-process deployment must not have files reaped under it."""
    import importlib

    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    conf = importlib.import_module("gunicorn_conf")

    called = []
    from prometheus_client import multiprocess

    monkeypatch.setattr(multiprocess, "mark_process_dead", lambda pid: called.append(pid))

    class _Worker:
        pid = 4242

    class _Server:
        class log:
            @staticmethod
            def warning(*a, **k):
                pass

    conf.child_exit(_Server(), _Worker())

    assert called == []


# --- the application's own metrics -----------------------------------------


def test_every_gauge_declares_a_multiprocess_mode():
    """Point 5, structurally.

    Without a mode `prometheus_client` emits one series **per process id**:
    four series where an alert expects one, which is worse than the
    undercounting this change is fixing.
    """
    tree = ast.parse((ROOT / "app" / "prom_metrics.py").read_text())
    missing = [
        (node.lineno, ast.unparse(node.targets[0]))
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and getattr(node.value.func, "id", None) == "Gauge"
        and not any(k.arg == "multiprocess_mode" for k in node.value.keywords)
    ]

    assert missing == [], f"Gauge without a declared multiprocess_mode: {missing}"


def test_no_metric_type_that_multiprocess_mode_silently_drops():
    """`Info` and `set_function` are accepted and then never collected.

    Both were measured, not read about: `Info` constructs and sets without
    error and is absent from the aggregated scrape; `set_function` is accepted
    and never evaluated. A metric that reports nothing while looking healthy is
    the failure this repository keeps finding, so neither is allowed here.
    """
    tree = ast.parse((ROOT / "app" / "prom_metrics.py").read_text())
    infos = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Info"
    ]
    assert infos == [], f"Info does not survive multiprocess mode; line {infos}"

    # Calls, found by AST -- not the substring. The first version of this scan
    # matched the word inside `app/metrics_endpoint.py`'s own docstring, which
    # explains why `set_function` is not used. A guard that flags the sentence
    # describing it is a guard nobody can satisfy.
    offenders = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        try:
            module = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(module):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "set_function":
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")

    assert offenders == [], (
        "set_function is accepted in multiprocess mode and never collected; "
        f"compute the value in app/metrics_endpoint.py instead. Found: {offenders}"
    )


def test_the_app_info_series_is_unchanged_by_the_move_off_Info():
    """The `Info` metric became a labelled Gauge. Same series, or it is a rename."""
    # In a subprocess with a clean environment. `prometheus_client` chooses its
    # value class from `PROMETHEUS_MULTIPROC_DIR` at import, so a test that
    # constructs metrics in-process inherits whatever an earlier test left set
    # -- which is how the first version of this failed, with a TypeError from
    # deep inside the library rather than a wrong answer.
    out = subprocess.run(
        [sys.executable, "-c", textwrap.dedent("""
            from prometheus_client import CollectorRegistry, Gauge, Info, generate_latest

            as_info = CollectorRegistry()
            Info("sora_app", "Application metadata", registry=as_info).info(
                {"version": "2.0.0", "platform": "SORA.Earth"})
            as_gauge = CollectorRegistry()
            Gauge("sora_app_info", "Application metadata", ["version", "platform"],
                  registry=as_gauge).labels(version="2.0.0", platform="SORA.Earth").set(1)

            def series(reg):
                return sorted(l for l in generate_latest(reg).decode().splitlines()
                              if not l.startswith("#"))

            print(series(as_info) == series(as_gauge))
            print(series(as_gauge))
        """)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/tmp"},
    )
    assert out.returncode == 0, out.stderr[-2000:]
    identical, rendered = out.stdout.splitlines()[:2]
    assert identical == "True", f"the series differ: {rendered}"
    assert 'sora_app_info{platform="SORA.Earth",version="2.0.0"} 1.0' in rendered, rendered


def test_the_scheduler_does_not_share_the_directory():
    """Point 7. The scheduler runs one process, serves no HTTP and is scraped by
    nothing; its lifetime must not be mixed into the backend's files."""
    entrypoint = (ROOT / "entrypoint.sh").read_text()
    override_at = entrypoint.index('exec "$@"')
    set_at = entrypoint.index("PROMETHEUS_MULTIPROC_DIR=")

    assert set_at > override_at, (
        "the variable is exported before the override branch, so the scheduler "
        "would inherit it"
    )

    compose = (ROOT / "docker-compose.prod.yml").read_text()
    assert "prometheus_multiproc" in compose, "the backend has no tmpfs for it"


def test_the_entrypoint_block_creates_and_clears_the_directory(tmp_path):
    """Runs the real lines out of `entrypoint.sh`, not a retyped copy.

    The block is extracted from the file by its own markers and executed, so a
    change to the script changes what this test runs. Retyping the commands
    here would test the copy, and the copy stays right when the script drifts.

    What it must do: create the directory, remove stale `.db` files left by a
    previous life of the container, and export the variable. Clearing belongs
    to the master, once, before the fork -- a worker clearing it on restart
    would wipe its siblings' counters.
    """
    script = (ROOT / "entrypoint.sh").read_text()
    start = script.index("# Prometheus multiprocess mode (#262).")
    end = script.index('echo "Starting server with Gunicorn')
    block = script[start:end]

    assert "mkdir -p" in block and "rm -f" in block, "the block no longer prepares the directory"

    target = tmp_path / "promdir"
    target.mkdir()
    stale = target / "gauge_all_999.db"
    stale.write_bytes(b"stale")

    probe = f'{block}\nprintenv PROMETHEUS_MULTIPROC_DIR\n'
    result = subprocess.run(
        ["bash", "-c", probe],
        capture_output=True, text=True, timeout=60,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
             "PROMETHEUS_MULTIPROC_DIR": str(target)},
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert str(target) in result.stdout, result.stdout
    assert not stale.exists(), "a stale .db file from a previous life survived"
    assert target.is_dir()


def test_the_entrypoint_refuses_an_unwritable_directory(tmp_path):
    """Fail loudly rather than serve with every metric write raising.

    With the variable exported and the directory unwritable, `prometheus_client`
    raises inside request handlers -- a running container answering 500s on the
    paths that touch a metric. Refusing to start is the cheaper failure.
    """
    script = (ROOT / "entrypoint.sh").read_text()
    start = script.index("# Prometheus multiprocess mode (#262).")
    end = script.index('echo "Starting server with Gunicorn')
    block = script[start:end]

    target = tmp_path / "readonly"
    target.mkdir()
    target.chmod(0o500)
    try:
        result = subprocess.run(
            ["bash", "-c", block],
            capture_output=True, text=True, timeout=60,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                 "PROMETHEUS_MULTIPROC_DIR": str(target)},
        )
    finally:
        target.chmod(0o700)

    assert result.returncode != 0, "an unwritable directory was accepted"
    assert "not writable" in result.stderr, result.stderr
