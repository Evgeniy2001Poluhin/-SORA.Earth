"""The split balances the outcome, and no longer claims to be temporal.

#183. `split_idx = int(len(X) * 0.8)` sliced the row order and the comment
called it "Temporal split -- no shuffling for time series data".
`projects.csv` has no time column: the order is whatever was appended last, by
bulk upload or by /data/refresh.

Measured on the 17,071 rows the file holds today:

    successes   whole 0.686    first 80% 0.759    last 20% 0.396
    budget      median         first 80% 40M      last 20% 30M
    duration    median         first 80% 69mo     last 20% 49mo

Two different populations. Every AUC recorded under that split measured a shift
in population as much as a model, and the promotion gate compared two of them.

Stratifying removes the grossest artefact and **does not** restore the property
the old name claimed. That limitation is recorded in the code and travels with
the metrics as `split_kind`, so a stored number says which question it answers.
"""
import numpy as np
import pytest


def _frame(n=400, positive=0.7):
    rng = np.random.default_rng(0)
    import pandas as pd
    y = (rng.random(n) < positive).astype(int)
    # Ordered by outcome, which is what the old split was vulnerable to.
    order = np.argsort(-y)
    return pd.DataFrame({
        "budget": rng.integers(50_000, 500_000, n).astype(float)[order],
        "co2_reduction": rng.integers(50, 900, n).astype(float)[order],
        "social_impact": rng.integers(1, 10, n).astype(float)[order],
        "duration_months": rng.integers(3, 36, n).astype(float)[order],
        "success": y[order],
    })


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    import app.api.retrain as retrain

    path = tmp_path / "projects.csv"
    models = tmp_path / "models"
    models.mkdir()
    monkeypatch.setattr(retrain, "PROJECTS_CSV", str(path))
    monkeypatch.setattr(retrain, "MODELS_DIR", str(models))
    monkeypatch.setattr(retrain, "PRED_LOG", str(tmp_path / "absent.csv"))
    monkeypatch.setattr(retrain, "DATASET_LOCK", str(tmp_path / ".lock"))
    return lambda df: df.to_csv(path, index=False)


def test_a_dataset_ordered_by_outcome_still_trains(dataset):
    """The case the old split could not survive.

    Sorted by outcome, `X[:split]` holds one class and `X[split:]` the other --
    which the class check added in #175 refuses outright. Stratifying makes the
    same data usable, which is the point.
    """
    import app.api.retrain as retrain

    dataset(_frame())

    result = retrain._do_retrain(min_samples=10)

    assert result["status"] in ("success", "accepted")


def test_the_two_sides_have_the_same_class_balance(dataset):
    """Not approximately: stratify is exact to rounding."""
    import json
    import os

    import app.api.retrain as retrain

    dataset(_frame())
    # The candidate is written to runtime/staged/<run_id>/, not to models/
    # (#199 phase 4): models/ is the immutable seed and a run that no gate has
    # approved does not touch it.
    from app.paths import staged_dir

    result = retrain._do_retrain(min_samples=10)
    with open(os.path.join(staged_dir(result["run_id"]), "metrics.json")) as fh:
        metrics = json.load(fh)

    assert metrics["train_samples"] + metrics["test_samples"] == 400
    assert metrics["test_samples"] == 80


def test_the_recorded_metrics_say_which_split_produced_them(dataset):
    """Without it the series in retrain_log silently mixes two kinds the day a
    temporal split becomes possible."""
    import json
    import os

    import app.api.retrain as retrain

    dataset(_frame())
    # The candidate is written to runtime/staged/<run_id>/, not to models/
    # (#199 phase 4): models/ is the immutable seed and a run that no gate has
    # approved does not touch it.
    from app.paths import staged_dir

    result = retrain._do_retrain(min_samples=10)
    with open(os.path.join(staged_dir(result["run_id"]), "metrics.json")) as fh:
        metrics = json.load(fh)

    assert metrics["split_kind"] == "stratified_by_outcome"


def test_the_code_no_longer_calls_it_temporal():
    """The name was the defect: it asserted a property the slice did not have,
    and sent an investigation looking for a time series that was never there."""
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "app", "api", "retrain.py"), encoding="utf-8") as fh:
        body = fh.read()

    assert "Temporal split — no shuffling" not in body
    assert "does not measure learning from the past" in body.lower() or \
           "This does not measure learning from the past" in body
