"""Seed, staged and active: which one is served, and can anyone tell (#191).

The decisions being implemented: seed ships with the repository, runtime lives
outside it, candidates are keyed by `run_id`, the champion is switched into
place atomically, a rejected candidate is never served, and losing the runtime
volume falls back to seed **with the fallback visible**.

The tests that matter are the negative ones. Serving a candidate that no gate
approved, or falling back silently, are the two failures this design exists to
prevent -- and both would look completely healthy from outside.
"""
import json
import os

import pytest

from app import model_source
from app.model_source import (
    ModelSource,
    activate,
    prune_staged,
    resolve_model_source,
)


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """A seed directory and an empty runtime, both outside the repository."""
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "model.pkl").write_bytes(b"seed-model")
    (seed / "meta.json").write_text(json.dumps({"retrained_at": "20260101_000000"}))

    runtime = tmp_path / "runtime"
    runtime.mkdir()

    monkeypatch.setenv("SORA_SEED_DIR", str(seed))
    monkeypatch.setenv("SORA_RUNTIME_DIR", str(runtime))
    return {"seed": seed, "runtime": runtime}


def stage(layout, run_id, version="20260816_120000", body=b"candidate"):
    path = layout["runtime"] / "staged" / run_id
    path.mkdir(parents=True)
    (path / "model.pkl").write_bytes(body)
    (path / "meta.json").write_text(json.dumps({"retrained_at": version}))
    return path


def test_with_no_active_the_seed_is_served_and_says_so(layout):
    resolved = resolve_model_source()

    assert resolved.source == "seed"
    assert resolved.version == "20260101_000000"
    assert resolved.fell_back is False, "a first start is not a fallback"


def test_a_staged_candidate_is_never_served(layout):
    """A rejected candidate must not reach traffic, so the serving path has no
    way to reach one at all."""
    stage(layout, "run-a")

    resolved = resolve_model_source()

    assert resolved.source == "seed"
    # Compared as a path, not as a substring: pytest's tmp_path carries the test
    # name, and this test's name contains the word "staged" — the first version
    # of this assertion failed on the directory name rather than on the model.
    assert os.path.realpath(resolved.path) == os.path.realpath(str(layout["seed"]))
    assert os.path.realpath(resolved.path) != os.path.realpath(
        str(layout["runtime"] / "staged" / "run-a"))


def test_activating_makes_the_candidate_the_champion(layout):
    stage(layout, "run-a", version="20260816_120000")

    activated = activate("run-a")
    resolved = resolve_model_source()

    assert activated.source == "active"
    assert resolved.source == "active"
    assert resolved.version == "20260816_120000"
    with open(os.path.join(resolved.path, "model.pkl"), "rb") as handle:
        assert handle.read() == b"candidate"


def test_activation_replaces_the_previous_champion_without_leaving_debris(layout):
    stage(layout, "run-a", version="20260816_120000", body=b"first")
    activate("run-a")
    stage(layout, "run-b", version="20260816_130000", body=b"second")
    activate("run-b")

    resolved = resolve_model_source()
    assert resolved.version == "20260816_130000"
    with open(os.path.join(resolved.path, "model.pkl"), "rb") as handle:
        assert handle.read() == b"second"

    leftovers = [n for n in os.listdir(layout["runtime"])
                 if n.startswith("active.")]
    assert leftovers == [], f"a half-swapped directory survived: {leftovers}"


def test_losing_the_runtime_volume_falls_back_to_seed(layout):
    """The scenario the fallback exists for."""
    stage(layout, "run-a")
    activate("run-a")
    assert resolve_model_source().source == "active"

    import shutil
    shutil.rmtree(layout["runtime"])

    resolved = resolve_model_source()
    assert resolved.source == "seed"
    assert resolved.version == "20260101_000000"


def test_a_half_written_active_is_refused_and_the_fallback_is_visible(layout, caplog):
    """Silent fallback is the failure: from outside it looks perfectly healthy.

    An operator seeing predictions has no way to tell they come from the
    bootstrap rather than the promoted model, and that difference is the whole
    content of months of retraining.
    """
    broken = layout["runtime"] / "active"
    broken.mkdir(parents=True)
    (broken / "meta.json").write_text(json.dumps({"retrained_at": "20260816_140000"}))

    with caplog.at_level("WARNING", logger="app.model_source"):
        resolved = resolve_model_source()

    assert resolved.source == "seed"
    assert resolved.fell_back is True, "the fallback was not reported"
    assert "model.pkl" in (resolved.reason or "")
    assert any("seed model" in r.getMessage() for r in caplog.records), (
        "nothing was logged; the fallback would be invisible"
    )


def test_activating_something_that_is_not_a_model_is_refused(layout):
    empty = layout["runtime"] / "staged" / "run-empty"
    empty.mkdir(parents=True)

    with pytest.raises(ValueError, match="has no model.pkl"):
        activate("run-empty")

    assert resolve_model_source().source == "seed", "a refused activation still changed active"


def test_a_run_id_cannot_escape_the_staged_directory(layout):
    """`run_id` reaches this from a journal row; a path separator in one would
    let it name any directory on the host."""
    from app.paths import staged_dir

    for hostile in ("../../etc", "a/b", "..", ""):
        with pytest.raises(ValueError, match="unusable run_id"):
            staged_dir(hostile)


def test_pruning_keeps_the_most_recent_and_removes_the_rest(layout):
    import time

    for index in range(4):
        stage(layout, f"run-{index}")
        time.sleep(0.01)

    removed = prune_staged(keep=2)

    remaining = sorted(os.listdir(layout["runtime"] / "staged"))
    assert remaining == ["run-2", "run-3"], remaining
    assert sorted(removed) == ["run-0", "run-1"]


def test_pruning_an_absent_directory_is_not_an_error(layout):
    assert prune_staged(keep=2) == []
