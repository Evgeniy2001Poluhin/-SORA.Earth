"""Staged candidates are bounded by what runs, not by a function that exists (#191).

`prune_staged` has been in `app/model_source.py` since the seed/staged/active
layout landed, and nothing but its own tests called it. Every retrain made a new
`runtime/staged/<run_id>/` and none was ever removed:

* a **rejected** candidate stayed, because only promotion touched staged;
* a **promoted** one stayed too, because `activate` copies rather than moves;
* a **failed** retrain stayed forever. `_do_retrain` re-raised without removing
  its directory, the directory kept its `.incomplete` marker, and pruning skips a
  marked directory unconditionally, so even a caller of `prune_staged` could
  never have reclaimed one. That includes the cheapest failures, "no training
  data" and "too few rows", which happen before a single model is written.

So retention is enforced where every candidate is created, at the start of
`_do_retrain` whichever of its callers ran it; a failed run removes what it
started; and a marker older than any retrain takes is read as a writer that died
rather than one still writing.

**No model is trained here.** `_do_retrain` is driven with no `projects.csv`, so
it fails at its first check inside the `try`, after it has created its staged
directory and before anything is fitted. The seed, the data and the runtime are
all under `tmp_path`, and the only thing it can write is that directory.
"""
import json
import os
import shutil
import time
import uuid
from contextlib import contextmanager

import pytest
from fastapi import HTTPException

import app.model_source as model_source
from app.model_source import INCOMPLETE_MARKER, prune_staged
from app.paths import ROOT_DIR, STAGED_RETENTION, runtime_dir

HOUR = 3600
WEEK = 7 * 24 * HOUR


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    # Imported before the environment is redirected. The module resolves its
    # paths once, at import: imported under this environment, `PROJECTS_CSV`
    # would point into this tmp_path for every test after this one -- and
    # monkeypatch would restore exactly that value. Measured: six tests in
    # test_training_does_not_promote.py failed with "No training data".
    import app.api.retrain as retrain

    seed = tmp_path / "models"
    seed.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    root = tmp_path / "runtime"

    monkeypatch.setenv("SORA_MODELS_DIR", str(seed))
    monkeypatch.setenv("SORA_SEED_DIR", str(seed))
    monkeypatch.setenv("SORA_DATA_DIR", str(data))
    monkeypatch.setenv("SORA_RUNTIME_DIR", str(root))

    monkeypatch.setattr(retrain, "PROJECTS_CSV", str(data / "projects.csv"))
    assert not (data / "projects.csv").exists()
    return {"root": root, "retrain": retrain}


def _candidate(root, age_seconds, incomplete=False):
    """A staged directory whose every timestamp is `age_seconds` old.

    Every one, not only the directory's: a candidate abandoned a week ago has an
    old marker and old files too, and ageing only one of them would let the
    result depend on which timestamp the implementation happens to read.
    """
    name = str(uuid.uuid4())
    path = root / "staged" / name
    path.mkdir(parents=True)
    written = [path / "model.pkl"]
    written[0].write_bytes(b"candidate")
    if incomplete:
        written.append(path / INCOMPLETE_MARKER)
        written[-1].write_text(name)
    stamp = time.time() - age_seconds
    # After every file is written: creating an entry moves the directory's mtime.
    for item in written + [path]:
        os.utime(item, (stamp, stamp))
    return name


def _staged(root):
    staged = root / "staged"
    return set(os.listdir(staged)) if staged.is_dir() else set()


def _crown(root, run_id):
    active = root / "active"
    active.mkdir(parents=True, exist_ok=True)
    (active / "activation.json").write_text(json.dumps({"run_id": run_id}))


def _failed_retrain(retrain):
    with pytest.raises(HTTPException):
        retrain._do_retrain(min_samples=50, trigger_source="test")


# ---------------------------------------------------------------- red before


def test_starting_a_retrain_prunes_candidates_beyond_retention(runtime):
    old = [_candidate(runtime["root"], age_seconds=(i + 1) * HOUR)
           for i in range(STAGED_RETENTION + 3)]
    newest = set(old[:STAGED_RETENTION])

    _failed_retrain(runtime["retrain"])

    survivors = _staged(runtime["root"]) & set(old)
    assert survivors == newest, (
        "%d of %d earlier candidates survived a retrain; retention is %d"
        % (len(survivors), len(old), STAGED_RETENTION)
    )


def test_a_failed_retrain_removes_the_directory_it_started(runtime):
    _failed_retrain(runtime["retrain"])
    assert _staged(runtime["root"]) == set(), (
        "a failed retrain left %s behind, and its .incomplete marker makes it unprunable"
        % sorted(_staged(runtime["root"]))
    )


def test_an_abandoned_marker_is_reclaimed_and_a_live_one_is_not(runtime):
    """Both states in one test: a rule that deletes every marker cannot pass."""
    abandoned = _candidate(runtime["root"], age_seconds=WEEK, incomplete=True)
    live = _candidate(runtime["root"], age_seconds=60, incomplete=True)

    prune_staged()

    remaining = _staged(runtime["root"])
    assert live in remaining, "a candidate was deleted underneath a writer that is still running"
    assert abandoned not in remaining, "a week-old unfinished candidate is kept forever"


def test_the_champion_is_read_after_the_lock_is_taken(runtime, monkeypatch):
    """Another process activates while this one waits for the lock.

    The lock stand-in performs that activation's observable effect -- the
    manifest now names a different run -- and then takes the real lock. Pruning
    must protect the champion as it is once the lock is held, not as it was
    before waiting.
    """
    root = runtime["root"]
    previous = _candidate(root, age_seconds=2 * HOUR)
    current = _candidate(root, age_seconds=HOUR)
    _crown(root, previous)

    real_lock = model_source._exclusive

    @contextmanager
    def lock_that_loses_a_race():
        _crown(root, current)
        with real_lock():
            yield

    monkeypatch.setattr(model_source, "_exclusive", lock_that_loses_a_race)
    prune_staged(keep=0)

    assert current in _staged(root), "pruning deleted the candidate that had just become champion"


def test_the_suite_keeps_its_runtime_out_of_the_repository():
    """The suite calls `_do_retrain` for real, and every call stages a candidate.

    conftest redirected `data/` and `models/` but not the runtime, so those
    candidates piled up in the repository's own `runtime/staged/` -- and with
    pruning wired into retraining, the suite would be deleting there as well.
    """
    runtime = os.path.realpath(runtime_dir())
    repository = os.path.realpath(ROOT_DIR)
    assert os.path.commonpath([runtime, repository]) != repository, (
        "the suite's runtime is %s, inside the repository" % runtime
    )


# ------------------------------------------------------ green before and after


def test_a_retrain_at_retention_removes_nothing(runtime):
    """Exactly `STAGED_RETENTION` earlier candidates: an off-by-one removes one."""
    old = {_candidate(runtime["root"], age_seconds=(i + 1) * HOUR)
           for i in range(STAGED_RETENTION)}

    _failed_retrain(runtime["retrain"])

    assert old <= _staged(runtime["root"]), "a candidate inside retention was removed"


def test_age_alone_removes_no_finished_candidate(runtime):
    """The age rule is about markers. A finished candidate is kept by count, however old."""
    old = {_candidate(runtime["root"], age_seconds=WEEK) for _ in range(2)}

    prune_staged()

    assert old <= _staged(runtime["root"])


def test_the_champion_survives_pruning_whatever_its_age(runtime):
    root = runtime["root"]
    champion = _candidate(root, age_seconds=WEEK)
    for i in range(STAGED_RETENTION + 2):
        _candidate(root, age_seconds=(i + 1) * HOUR)
    _crown(root, champion)

    prune_staged()

    assert champion in _staged(root)


def test_a_pruning_failure_does_not_stop_the_retrain(runtime, monkeypatch):
    """Retention is housekeeping.

    A candidate that cannot be removed -- a file on the volume owned by another
    user, say -- must not stop every retrain from then on. The failure is made
    real and checked to occur before the retrain is asked to survive it.
    """
    root = runtime["root"]
    stuck = _candidate(root, age_seconds=WEEK)
    for i in range(STAGED_RETENTION):
        _candidate(root, age_seconds=(i + 1) * HOUR)

    real_rmtree = shutil.rmtree

    def rmtree_refusing_one(path, *args, **kwargs):
        if os.path.basename(os.fspath(path).rstrip(os.sep)) == stuck:
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", rmtree_refusing_one)
    with pytest.raises(PermissionError):
        prune_staged()

    # Its own refusal -- no training data -- and not the pruning's.
    _failed_retrain(runtime["retrain"])
