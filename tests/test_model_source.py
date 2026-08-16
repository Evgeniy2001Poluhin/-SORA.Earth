"""Seed, staged and active: which is served, and what an interruption leaves (#191).

The decisions: seed ships with the repository, runtime lives outside it,
candidates are keyed by `run_id`, the champion is switched atomically, a
rejected candidate is never served, and losing the runtime volume falls back to
seed **with the fallback visible**.

The tests that carry the weight are the negative ones. Serving a candidate no
gate approved, falling back silently, and a half-finished swap are the three
failures this design exists to prevent — and all three look perfectly healthy
from outside.

Activation is interrupted at each of its transitions, and each interruption is
asserted recoverable, because "atomic" is a claim about what a crash leaves
behind and cannot be checked any other way.
"""
import json
import os
import shutil
import uuid

import pytest

from app import model_source
from app.model_source import (
    INCOMPLETE_MARKER,
    REQUIRED_ARTEFACTS,
    activate,
    prune_staged,
    recover,
    resolve_model_source,
    validate_run_id,
)


def write_model(path, version, body=b"model"):
    path.mkdir(parents=True, exist_ok=True)
    (path / "model.pkl").write_bytes(body)
    (path / "scaler.pkl").write_bytes(b"scaler")
    (path / "best_threshold.pkl").write_bytes(b"threshold")
    (path / "meta.json").write_text(json.dumps({"retrained_at": version}))
    return path


@pytest.fixture
def layout(tmp_path, monkeypatch):
    seed = write_model(tmp_path / "seed", "20260101_000000", b"seed-model")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("SORA_SEED_DIR", str(seed))
    monkeypatch.setenv("SORA_RUNTIME_DIR", str(runtime))
    return {"seed": seed, "runtime": runtime}


def a_run():
    return str(uuid.uuid4())


def stage(layout, run_id, version="20260816_120000", body=b"candidate"):
    return write_model(layout["runtime"] / "staged" / run_id, version, body)


def active_body(layout):
    with open(layout["runtime"] / "active" / "model.pkl", "rb") as handle:
        return handle.read()


# ----------------------------------------------------------------- resolution

def test_with_no_active_the_seed_is_served_and_that_is_not_a_fallback(layout):
    resolved = resolve_model_source()

    assert resolved.source == "seed"
    assert resolved.version == "20260101_000000"
    assert resolved.fell_back is False


def test_a_staged_candidate_is_never_served(layout):
    """The serving path has no way to reach one, which is the guarantee."""
    run = a_run()
    stage(layout, run)

    resolved = resolve_model_source()

    assert resolved.source == "seed"
    assert resolved.version == "20260101_000000", "a candidate's version was served"


def test_an_incomplete_active_falls_back_and_names_what_is_missing(layout, caplog):
    """Checking one file was not enough: a copy interrupted after model.pkl and
    before scaler.pkl would have passed, and features would then be scaled by
    the previous model's scaler."""
    partial = layout["runtime"] / "active"
    partial.mkdir(parents=True)
    (partial / "model.pkl").write_bytes(b"half")

    with caplog.at_level("WARNING", logger="app.model_source"):
        resolved = resolve_model_source()

    assert resolved.source == "seed"
    assert resolved.fell_back is True, "the fallback was not reported"
    assert "scaler.pkl" in resolved.reason
    assert any("seed model" in r.getMessage() for r in caplog.records)


def test_nothing_reported_carries_an_absolute_path(layout, caplog):
    """`/health` and the log must not publish the host's filesystem layout."""
    partial = layout["runtime"] / "active"
    partial.mkdir(parents=True)
    (partial / "model.pkl").write_bytes(b"half")

    with caplog.at_level("WARNING", logger="app.model_source"):
        resolved = resolve_model_source()

    assert not hasattr(resolved, "path"), "the source still exposes a path"
    for field in (resolved.source, resolved.version or "", resolved.reason or ""):
        assert str(layout["runtime"]) not in field
    for record in caplog.records:
        assert str(layout["runtime"]) not in record.getMessage()


def test_losing_the_runtime_volume_falls_back_to_seed(layout):
    run = a_run()
    stage(layout, run)
    activate(run)
    assert resolve_model_source().source == "active"

    shutil.rmtree(layout["runtime"])

    resolved = resolve_model_source()
    assert resolved.source == "seed"
    assert resolved.version == "20260101_000000"


# ----------------------------------------------------------------- activation

def test_activating_makes_the_candidate_the_champion(layout):
    run = a_run()
    stage(layout, run, version="20260816_120000")

    activate(run)

    resolved = resolve_model_source()
    assert resolved.source == "active"
    assert resolved.version == "20260816_120000"
    assert active_body(layout) == b"candidate"


def test_the_swap_happens_within_one_filesystem(layout):
    """`rename` is atomic only within a filesystem, so the staging area for the
    swap must be a sibling of `active`, never a temp directory elsewhere."""
    run = a_run()
    stage(layout, run)
    activate(run)

    from app.paths import active_dir
    assert os.path.dirname(active_dir()) == str(layout["runtime"])


def test_a_second_activation_replaces_the_first_and_leaves_no_debris(layout):
    first, second = a_run(), a_run()
    stage(layout, first, version="20260816_120000", body=b"first")
    activate(first)
    stage(layout, second, version="20260816_130000", body=b"second")
    activate(second)

    assert active_body(layout) == b"second"
    leftovers = [n for n in os.listdir(layout["runtime"]) if n.startswith("active.")]
    assert leftovers == [], f"a half-swapped directory survived: {leftovers}"


def test_an_incomplete_candidate_is_refused_and_active_is_untouched(layout):
    run = a_run()
    stage(layout, run, body=b"good")
    activate(run)

    broken = a_run()
    path = layout["runtime"] / "staged" / broken
    path.mkdir(parents=True)
    (path / "model.pkl").write_bytes(b"partial")

    with pytest.raises(ValueError, match="not a complete model"):
        activate(broken)

    assert active_body(layout) == b"good", "a refused activation changed the champion"


def test_a_candidate_still_being_written_is_refused(layout):
    run = a_run()
    path = stage(layout, run)
    (path / INCOMPLETE_MARKER).write_text("")

    with pytest.raises(ValueError, match="still being written"):
        activate(run)


# ------------------------------------------------------------ crash recovery

def test_an_interrupted_build_is_discarded_and_the_champion_survives(layout):
    """Crash after `active.next` is assembled, before either rename.

    `next` was built but never chosen, and a crash is not an approval — so
    recovery discards it rather than promoting it.
    """
    run = a_run()
    stage(layout, run, body=b"champion")
    activate(run)

    write_model(layout["runtime"] / "active.next", "20260816_999999", b"unchosen")

    outcome = recover()

    assert "discarded" in outcome
    assert not os.path.exists(layout["runtime"] / "active.next")
    assert active_body(layout) == b"champion"


def test_an_interruption_between_the_two_renames_restores_the_previous(layout):
    """Crash after `active -> active.previous`, before `next -> active`.

    Without recovery this leaves no `active` at all, and the platform would
    quietly serve seed while a perfectly good champion sat in `previous`.
    """
    run = a_run()
    stage(layout, run, version="20260816_120000", body=b"champion")
    activate(run)

    os.rename(layout["runtime"] / "active", layout["runtime"] / "active.previous")
    assert resolve_model_source().source == "seed", "the state under test was not reached"

    outcome = recover()

    assert "restored" in outcome
    resolved = resolve_model_source()
    assert resolved.source == "active"
    assert resolved.version == "20260816_120000"
    assert active_body(layout) == b"champion"


def test_an_interruption_before_cleanup_drops_the_leftover(layout):
    """Crash after both renames, before `previous` is removed. The swap
    completed, so the new champion stays and the leftover goes."""
    first, second = a_run(), a_run()
    stage(layout, first, body=b"old")
    activate(first)
    stage(layout, second, version="20260816_130000", body=b"new")
    activate(second)

    write_model(layout["runtime"] / "active.previous", "20260816_120000", b"old")

    outcome = recover()

    assert "leftover" in outcome
    assert not os.path.exists(layout["runtime"] / "active.previous")
    assert active_body(layout) == b"new"


def test_recovery_on_a_healthy_layout_changes_nothing(layout):
    run = a_run()
    stage(layout, run)
    activate(run)

    assert recover() is None
    assert resolve_model_source().source == "active"


# ------------------------------------------------------------------ run ids

@pytest.mark.parametrize("hostile", [
    "../../etc", "a/b", "..", ".", "", "active", "~", "run-1",
    "0000-0000", "no-uuid-here", None, 7,
])
def test_only_a_canonical_uuid_may_become_a_directory_name(hostile):
    """A run id arrives from a journal row and becomes a path. Screening for
    separators alone would still admit `.`, `active`, or a control character."""
    with pytest.raises(ValueError):
        validate_run_id(hostile)


def test_a_real_run_id_is_accepted(layout):
    run = a_run()
    assert validate_run_id(run) == run


# ------------------------------------------------------------------- pruning

def test_pruning_keeps_the_most_recent_and_removes_the_rest(layout):
    import time

    runs = []
    for index in range(4):
        run = a_run()
        stage(layout, run, version=f"2026081{index}_000000")
        runs.append(run)
        time.sleep(0.01)

    removed = prune_staged(keep=2)

    remaining = sorted(os.listdir(layout["runtime"] / "staged"))
    assert sorted(remaining) == sorted(runs[2:])
    assert sorted(removed) == sorted(runs[:2])


def test_pruning_never_removes_the_candidate_the_champion_came_from(layout):
    """A rollback needs something to roll back to."""
    import time

    champion = a_run()
    stage(layout, champion, version="20260816_120000")
    activate(champion)
    time.sleep(0.01)

    for index in range(3):
        stage(layout, a_run(), version=f"2026081{index}_999999")
        time.sleep(0.01)

    prune_staged(keep=1)

    assert champion in os.listdir(layout["runtime"] / "staged"), (
        "the champion's own candidate was pruned; a rollback has nothing to reach"
    )


def test_pruning_never_removes_a_directory_still_being_written(layout):
    import time

    writing = a_run()
    path = stage(layout, writing)
    (path / INCOMPLETE_MARKER).write_text("")
    time.sleep(0.01)
    for _ in range(3):
        stage(layout, a_run())
        time.sleep(0.01)

    prune_staged(keep=1)

    assert writing in os.listdir(layout["runtime"] / "staged"), (
        "a candidate was deleted underneath its writer"
    )


def test_pruning_never_removes_an_explicitly_protected_run(layout):
    """The run being activated, or one a retry is about to re-register."""
    import time

    protected = a_run()
    stage(layout, protected)
    time.sleep(0.01)
    for _ in range(3):
        stage(layout, a_run())
        time.sleep(0.01)

    prune_staged(keep=1, protect=[protected])

    assert protected in os.listdir(layout["runtime"] / "staged")


def test_pruning_an_absent_directory_is_not_an_error(layout):
    assert prune_staged(keep=2) == []


# ------------------------------------------------------------------ locking

def test_the_lock_is_held_across_processes_not_just_threads(layout):
    """The backend and the scheduler are separate containers.

    A lock that only excluded threads would let two containers interleave their
    renames into a state recovery could not tell from a crash.
    """
    import subprocess
    import sys
    import textwrap

    run = a_run()
    stage(layout, run)

    probe = textwrap.dedent(f"""
        import fcntl, os, sys
        path = {model_source._lock_path()!r}
        handle = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            sys.exit(0)   # got it: nobody was holding it
        except BlockingIOError:
            sys.exit(3)   # held by another process, which is the point
    """)

    with model_source._exclusive():
        result = subprocess.run([sys.executable, "-c", probe], capture_output=True)

    assert result.returncode == 3, (
        "another process acquired the lock while it was held; activations from "
        "the backend and the scheduler could interleave"
    )
