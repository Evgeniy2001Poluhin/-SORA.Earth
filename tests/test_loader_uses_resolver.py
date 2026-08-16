"""The champion is loaded from one source, whole, and says which (#191 phase 3).

The failure this prevents is undetectable downstream: a model from one training
run with a scaler from another scales the features into a space the model was
not fitted on. The probabilities stay in [0, 1], every check passes, and the
numbers are wrong.

Presence is not enough to rule that out — `resolve_model_source` checks files
exist, and a pickle can still be truncated — so the load is transactional and a
failure part-way through `active` falls back to `seed` as a whole.
"""
import json
import os
import pickle
import uuid

import pytest

from app.model_loader import CHAMPION_ARTEFACTS, health_fields, load_champion
from app.model_source import activate


class _Scaler:
    """Distinguishable by value, so a mixed set is visible in the assertion."""

    def __init__(self, tag):
        self.tag = tag


class _Model:
    def __init__(self, tag):
        self.tag = tag

    def predict_proba(self, _frame):
        return [[0.4, 0.6]]


def write_set(path, tag, version):
    path.mkdir(parents=True, exist_ok=True)
    with open(path / "model.pkl", "wb") as handle:
        pickle.dump(_Model(tag), handle)
    with open(path / "scaler.pkl", "wb") as handle:
        pickle.dump(_Scaler(tag), handle)
    with open(path / "best_threshold.pkl", "wb") as handle:
        pickle.dump({"threshold": 0.55}, handle)
    (path / "meta.json").write_text(json.dumps({"retrained_at": version}))
    (path / "metrics.json").write_text(json.dumps({"roc_auc": 0.9}))
    return path


@pytest.fixture
def layout(tmp_path, monkeypatch):
    seed = write_set(tmp_path / "seed", "seed", "20260101_000000")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("SORA_SEED_DIR", str(seed))
    monkeypatch.setenv("SORA_RUNTIME_DIR", str(runtime))
    return {"seed": seed, "runtime": runtime}


def stage_and_activate(layout, tag, version):
    run = str(uuid.uuid4())
    write_set(layout["runtime"] / "staged" / run, tag, version)
    activate(run)
    return run


def test_the_active_model_is_loaded_and_can_predict(layout):
    run = stage_and_activate(layout, "champion", "20260816_120000")

    champion = load_champion()

    assert champion.source.source == "active"
    assert champion.source.run_id == run
    assert champion.source.version == "20260816_120000"
    assert champion.model.predict_proba(None) == [[0.4, 0.6]]
    assert champion.best_threshold == 0.55


def test_every_piece_comes_from_the_same_place(layout):
    """The whole point. A model and a scaler from different runs cannot be
    detected downstream — the probabilities stay in [0, 1] and every check
    passes."""
    stage_and_activate(layout, "champion", "20260816_120000")

    champion = load_champion()

    assert champion.model.tag == "champion"
    assert champion.scaler.tag == "champion", "the scaler came from a different source"
    assert champion.meta["retrained_at"] == "20260816_120000"


def test_with_no_active_the_seed_serves_and_predicts(layout):
    champion = load_champion()

    assert champion.source.source == "seed"
    assert champion.source.fell_back is False, "a first start is not a fallback"
    assert champion.model.tag == "seed"
    assert champion.scaler.tag == "seed"
    assert champion.model.predict_proba(None) == [[0.4, 0.6]]


def test_losing_the_active_model_falls_back_to_seed_whole(layout):
    import shutil

    stage_and_activate(layout, "champion", "20260816_120000")
    assert load_champion().source.source == "active"

    shutil.rmtree(layout["runtime"] / "active")

    champion = load_champion()
    assert champion.source.source == "seed"
    assert champion.model.tag == "seed"
    assert champion.scaler.tag == "seed"


def test_an_incomplete_active_falls_back_and_warns(layout, caplog):
    partial = layout["runtime"] / "active"
    partial.mkdir(parents=True)
    with open(partial / "model.pkl", "wb") as handle:
        pickle.dump(_Model("half"), handle)

    with caplog.at_level("WARNING"):
        champion = load_champion()

    assert champion.source.source == "seed"
    assert champion.source.fell_back is True
    assert champion.model.tag == "seed", "a half-written active model was served"
    assert any("seed model" in r.getMessage() for r in caplog.records), (
        "the fallback was invisible"
    )


def test_an_unreadable_active_model_falls_back_wholly_not_partly(layout, caplog):
    """Present but corrupt. Presence checks cannot catch this, so the load
    itself has to be transactional."""
    stage_and_activate(layout, "champion", "20260816_120000")
    # Truncate the scaler, leaving every file present.
    (layout["runtime"] / "active" / "scaler.pkl").write_bytes(b"\x80\x04 truncated")

    with caplog.at_level("WARNING"):
        champion = load_champion()

    assert champion.source.source == "seed"
    assert champion.source.fell_back is True
    assert champion.model.tag == "seed", "the model came from active and the scaler from seed"
    assert champion.scaler.tag == "seed"
    assert any("could not be loaded" in r.getMessage() for r in caplog.records)


def test_a_staged_candidate_is_never_loaded(layout):
    """Not activated, so it must not reach serving at all."""
    run = str(uuid.uuid4())
    write_set(layout["runtime"] / "staged" / run, "candidate", "20260816_999999")

    champion = load_champion()

    assert champion.source.source == "seed"
    assert champion.model.tag == "seed", "a candidate no gate approved was served"


def test_recovery_runs_at_load_and_can_be_turned_off(layout):
    """It takes a lock and may rename directories, so it belongs at process
    start and before an explicit reload — not in front of every inference."""
    stage_and_activate(layout, "champion", "20260816_120000")
    # An interrupted swap: active is gone, previous holds the champion.
    os.rename(layout["runtime"] / "active", layout["runtime"] / "active.previous")

    without = load_champion(recover_first=False)
    assert without.source.source == "seed", "recovery ran when it was not asked to"

    with_recovery = load_champion(recover_first=True)
    assert with_recovery.source.source == "active"
    assert with_recovery.model.tag == "champion"


def test_health_fields_never_carry_a_path(layout):
    """/health is unauthenticated; the host's filesystem layout is not something
    it should publish."""
    stage_and_activate(layout, "champion", "20260816_120000")
    champion = load_champion()

    fields = health_fields(champion.source)

    assert set(fields) == {"source", "run_id", "model_version", "fell_back", "reason_code"}
    for value in fields.values():
        assert str(layout["runtime"]) not in str(value)
        assert str(layout["seed"]) not in str(value)


def test_health_reports_the_fallback_so_it_is_not_invisible(layout):
    partial = layout["runtime"] / "active"
    partial.mkdir(parents=True)
    (partial / "model.pkl").write_bytes(b"half")

    fields = health_fields(load_champion().source)

    assert fields["source"] == "seed"
    assert fields["fell_back"] is True
    # A stable code, not a list of filenames: /health is public, and the
    # artefact set is an implementation detail that will change.
    assert fields["reason_code"] == "active_incomplete"
    assert "scaler.pkl" not in str(fields), "the public response names internal files"


def test_the_champion_set_is_the_retrained_one(layout):
    """Stated explicitly: xgb, stacking and the v2 ensemble are different
    models, not parts of this one, and keep loading from seed."""
    assert set(CHAMPION_ARTEFACTS) == {
        "model.pkl", "scaler.pkl", "best_threshold.pkl", "meta.json"}
    assert "xgb_model.pkl" not in CHAMPION_ARTEFACTS
    assert "ensemble_model_v2.pkl" not in CHAMPION_ARTEFACTS


def test_a_corrupt_active_still_reports_the_version_actually_being_served(layout):
    """The bug this replaces: /health showed the fallback and lost the version.

    After `active` fails to load, the resolver has no seed version to hand over,
    so taking it from there reported None — while a perfectly identifiable seed
    model was answering every request.
    """
    stage_and_activate(layout, "champion", "20260816_120000")
    (layout["runtime"] / "active" / "scaler.pkl").write_bytes(b"\x80\x04 truncated")

    champion = load_champion()
    fields = health_fields(champion.source)

    assert fields["source"] == "seed"
    assert fields["fell_back"] is True
    assert fields["reason_code"] == "active_unreadable"
    assert fields["model_version"] == "20260101_000000", (
        "the version of the model actually serving was lost"
    )


def test_the_public_response_never_names_an_internal_file(layout):
    """`missing scaler.pkl, best_threshold.pkl` would tie the API to the current
    artefact set and change whenever that set does."""
    partial = layout["runtime"] / "active"
    partial.mkdir(parents=True)
    with open(partial / "model.pkl", "wb") as handle:
        pickle.dump(_Model("half"), handle)

    fields = health_fields(load_champion().source)

    published = " ".join(str(v) for v in fields.values())
    for internal in ("model.pkl", "scaler.pkl", "best_threshold.pkl", "meta.json"):
        assert internal not in published, f"the response names {internal}"
    assert fields["reason_code"] in ("active_incomplete", "active_being_written")


def test_the_required_set_has_one_definition(layout):
    """Two independent lists of 'what a model is' drift, and the drift shows up
    as a directory that passes the presence check and then fails to load."""
    from app.model_source import REQUIRED_ARTEFACTS

    assert CHAMPION_ARTEFACTS is REQUIRED_ARTEFACTS
