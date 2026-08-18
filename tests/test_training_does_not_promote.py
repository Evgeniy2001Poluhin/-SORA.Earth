"""Training a model no longer makes it the champion (#199 phase 4).

Measured before this change, on a real cycle: `_do_retrain` wrote `model.pkl`
into `models/` and assigned `rf_model` into `app.main` — so a model became the
serving one by the fact of having been trained, before the AUC gate, the
confidence bound, the registry check or the degradation check had run. A
rejection then recorded a decision about an artefact that was already answering
requests, and `models/`, the immutable bootstrap a lost runtime volume falls
back to, was overwritten every run.

The candidate now goes to `runtime/staged/<run_id>/` and only a gate's pass
reaches `runtime/active/`.
"""
import json
import os

import numpy as np
import pandas as pd
import pytest

from app.paths import active_dir, seed_dir, staged_dir


def _frame(rows=400):
    rng = np.random.default_rng(5)
    return pd.DataFrame({
        "budget": rng.uniform(1000, 900000, rows),
        "co2_reduction": rng.uniform(1, 9000, rows),
        "social_impact": rng.uniform(1, 99, rows),
        "duration_months": rng.integers(1, 119, rows),
        "success": ([1] * (rows // 5)) + ([0] * (rows - rows // 5)),
    })


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """A seed we can fingerprint, and a runtime nothing has touched."""
    seed = tmp_path / "models"
    seed.mkdir()
    for name in ("model.pkl", "scaler.pkl", "best_threshold.pkl"):
        (seed / name).write_bytes(b"seed-" + name.encode())
    (seed / "meta.json").write_text(json.dumps({"retrained_at": "20260101_000000"}))
    (seed / "metrics.json").write_text(json.dumps({"roc_auc": 0.5}))

    data = tmp_path / "data"
    data.mkdir()
    _frame().to_csv(data / "projects.csv", index=False)

    monkeypatch.setenv("SORA_MODELS_DIR", str(seed))
    monkeypatch.setenv("SORA_SEED_DIR", str(seed))
    monkeypatch.setenv("SORA_DATA_DIR", str(data))
    monkeypatch.setenv("SORA_RUNTIME_DIR", str(tmp_path / "runtime"))

    import app.api.retrain as retrain
    monkeypatch.setattr(retrain, "MODELS_DIR", str(seed))
    monkeypatch.setattr(retrain, "DATA_DIR", str(data), raising=False)
    return {"seed": seed, "runtime": tmp_path / "runtime", "retrain": retrain}


def fingerprint(directory):
    import hashlib

    digest = {}
    for name in sorted(os.listdir(directory)):
        full = os.path.join(directory, name)
        if os.path.isfile(full):
            with open(full, "rb") as handle:
                digest[name] = hashlib.sha256(handle.read()).hexdigest()
    return digest


def test_training_writes_the_candidate_to_staged_and_leaves_the_seed_alone(isolated):
    before = fingerprint(isolated["seed"])

    result = isolated["retrain"]._do_retrain(min_samples=10)

    run = result["run_id"]
    staged = staged_dir(run)
    assert os.path.exists(os.path.join(staged, "model.pkl")), "no candidate was staged"
    assert result.get("staged") is True

    assert fingerprint(isolated["seed"]) == before, (
        "training overwrote the seed — the bootstrap a lost runtime falls back to"
    )


def test_training_does_not_make_the_model_active(isolated):
    """The whole point. `active/` is written by `activate()` and nothing else."""
    isolated["retrain"]._do_retrain(min_samples=10)

    assert not os.path.exists(active_dir()), (
        "training promoted itself; a gate has not spoken yet"
    )


def test_the_completed_candidate_carries_no_incomplete_marker(isolated):
    """It is left in place for as long as the directory is being assembled, so a
    crash mid-write leaves something activation and pruning both refuse."""
    from app.model_source import INCOMPLETE_MARKER

    result = isolated["retrain"]._do_retrain(min_samples=10)

    staged = staged_dir(result["run_id"])
    assert not os.path.exists(os.path.join(staged, INCOMPLETE_MARKER))


def test_the_staged_candidate_is_a_complete_model(isolated):
    """Complete by the resolver's own definition, or activation would refuse it
    and the gate's decision would have nowhere to go."""
    from app.model_source import REQUIRED_ARTEFACTS

    result = isolated["retrain"]._do_retrain(min_samples=10)
    staged = staged_dir(result["run_id"])

    missing = [n for n in REQUIRED_ARTEFACTS
               if not os.path.exists(os.path.join(staged, n))]
    assert not missing, f"the staged candidate cannot be activated: missing {missing}"


def test_two_runs_do_not_overwrite_each_other(isolated):
    """Fixed filenames are why a past run's model could not be re-registered:
    `model.pkl` was replaced by the next retrain (#189)."""
    first = isolated["retrain"]._do_retrain(min_samples=10)["run_id"]
    second = isolated["retrain"]._do_retrain(min_samples=10)["run_id"]

    assert first != second
    assert os.path.exists(os.path.join(staged_dir(first), "model.pkl"))
    assert os.path.exists(os.path.join(staged_dir(second), "model.pkl"))


def test_activation_is_what_makes_a_candidate_serve(isolated):
    """End to end: staged, then activated, then resolving finds it."""
    from app.model_source import activate, resolve_model_source

    assert resolve_model_source().source == "seed"

    run = isolated["retrain"]._do_retrain(min_samples=10)["run_id"]
    assert resolve_model_source().source == "seed", "staging alone changed the source"

    activate(run)

    resolved = resolve_model_source()
    assert resolved.source == "active"
    assert resolved.run_id == run
