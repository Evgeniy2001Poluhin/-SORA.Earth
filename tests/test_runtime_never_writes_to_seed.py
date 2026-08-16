"""Nothing at runtime writes to the seed directory (#191).

`models/` is the seed and is immutable while the process runs: the only writer
of anything under it is a commit. Stating that is not enough — on 2026-08-16 a
probe overwrote six tracked files under `models/` in a single run, and it was
caught by `git status`, not by any check.

These run the mutating operations against a real layout and assert the seed
directory comes out byte-identical.
"""
import hashlib
import json
import os
import uuid

import pytest

from app.model_source import activate, prune_staged, recover, resolve_model_source


def write_model(path, version, body=b"model"):
    path.mkdir(parents=True, exist_ok=True)
    (path / "model.pkl").write_bytes(body)
    (path / "scaler.pkl").write_bytes(b"scaler")
    (path / "best_threshold.pkl").write_bytes(b"threshold")
    (path / "meta.json").write_text(json.dumps({"retrained_at": version}))
    return path


def fingerprint(directory):
    """Every file under the directory, by name and content."""
    digest = {}
    for root, _dirs, files in os.walk(directory):
        for name in sorted(files):
            full = os.path.join(root, name)
            with open(full, "rb") as handle:
                digest[os.path.relpath(full, directory)] = hashlib.sha256(
                    handle.read()).hexdigest()
    return digest


@pytest.fixture
def layout(tmp_path, monkeypatch):
    seed = write_model(tmp_path / "seed", "20260101_000000", b"seed-model")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("SORA_SEED_DIR", str(seed))
    monkeypatch.setenv("SORA_RUNTIME_DIR", str(runtime))
    return {"seed": seed, "runtime": runtime}


def test_activation_leaves_the_seed_untouched(layout):
    before = fingerprint(layout["seed"])

    run = str(uuid.uuid4())
    write_model(layout["runtime"] / "staged" / run, "20260816_120000", b"candidate")
    activate(run)

    assert fingerprint(layout["seed"]) == before, "activation wrote into the seed"


def test_pruning_leaves_the_seed_untouched(layout):
    before = fingerprint(layout["seed"])

    for _ in range(4):
        write_model(layout["runtime"] / "staged" / str(uuid.uuid4()), "20260816_120000")
    prune_staged(keep=1)

    assert fingerprint(layout["seed"]) == before, "pruning wrote into the seed"


def test_recovery_leaves_the_seed_untouched(layout):
    run = str(uuid.uuid4())
    write_model(layout["runtime"] / "staged" / run, "20260816_120000")
    activate(run)
    before = fingerprint(layout["seed"])

    os.rename(layout["runtime"] / "active", layout["runtime"] / "active.previous")
    recover()

    assert fingerprint(layout["seed"]) == before, "recovery wrote into the seed"


def test_resolving_the_source_leaves_the_seed_untouched(layout):
    before = fingerprint(layout["seed"])

    resolve_model_source()

    assert fingerprint(layout["seed"]) == before, "resolution wrote into the seed"


def test_the_lock_file_is_not_placed_in_the_seed(layout):
    """It has to live where both containers can open the same inode, and that is
    the runtime volume — not the immutable directory shipped in git."""
    from app.model_source import _lock_path

    run = str(uuid.uuid4())
    write_model(layout["runtime"] / "staged" / run, "20260816_120000")
    activate(run)

    assert str(layout["seed"]) not in _lock_path()
    assert os.path.exists(_lock_path()), "the lock file was never created"
    assert not any(name.startswith(".model-source")
                   for name in os.listdir(layout["seed"])), (
        "a lock file was created in the seed directory"
    )
