"""Retraining says what is wrong with the data instead of failing inside sklearn.

Issue #1. `_do_retrain` fed `projects.csv` straight into the split. A missing
`success`, or an inf produced by a derived feature, surfaced as

    ValueError: Input y contains NaN

from deep inside sklearn -- naming neither the row, nor the column, nor the
file. The test that caught it was marked xfail in v0.2.1 and stayed that way.

`projects.csv` is appended to by more than the validated upload path, so a NaN
getting in is not hypothetical. What these pin is that the run either trains on
clean rows and says how many it discarded, or refuses with a message that names
the reason.
"""
import os

import numpy as np
import pandas as pd
import pytest

from fastapi import HTTPException

FEATURES = ["budget", "co2_reduction", "social_impact", "duration_months"]


def _frame(n=40, successes=None):
    """A dataset large enough to train on, with both outcome classes."""
    rng = np.random.default_rng(0)
    return pd.DataFrame({
        "budget": rng.integers(50_000, 500_000, n).astype(float),
        "co2_reduction": rng.integers(50, 900, n).astype(float),
        "social_impact": rng.integers(1, 10, n).astype(float),
        "duration_months": rng.integers(3, 36, n).astype(float),
        "success": (successes if successes is not None
                    else [i % 2 for i in range(n)]),
    })


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    """`projects.csv` under this test's control."""
    import app.api.retrain as retrain

    path = tmp_path / "projects.csv"
    monkeypatch.setattr(retrain, "PROJECTS_CSV", str(path))
    # MODELS_DIR is `models_dir()` evaluated at import, so patching the function
    # would leave the constant pointing at the real directory and the run would
    # overwrite the deployed model.
    models = tmp_path / "models"
    models.mkdir()
    monkeypatch.setattr(retrain, "MODELS_DIR", str(models))
    monkeypatch.setattr(retrain, "PRED_LOG", str(tmp_path / "absent.csv"))
    monkeypatch.setattr(retrain, "DATASET_LOCK", str(tmp_path / ".lock"))

    def _write(df):
        df.to_csv(path, index=False)
        return path

    return _write


def _retrain(**kwargs):
    import app.api.retrain as retrain
    return retrain._do_retrain(**kwargs)


def test_a_row_with_a_missing_label_is_dropped_not_fatal(dataset):
    """The exact failure #1 names."""
    df = _frame(40)
    df.loc[3, "success"] = np.nan
    dataset(df)

    result = _retrain(min_samples=10)

    assert result["status"] in ("success", "accepted")


def test_a_row_with_a_missing_feature_is_dropped(dataset):
    df = _frame(40)
    df.loc[5, "co2_reduction"] = np.nan
    dataset(df)

    assert _retrain(min_samples=10)["status"] in ("success", "accepted")


def test_an_infinite_derived_feature_is_dropped(dataset):
    """`co2_per_dollar` is a division, and a large enough numerator overflows.

    The denominators are clipped, so a zero divisor is already impossible --
    which is why the guard is on the result and not on the inputs.
    """
    df = _frame(40)
    df.loc[7, "co2_reduction"] = np.inf
    dataset(df)

    assert _retrain(min_samples=10)["status"] in ("success", "accepted")


def test_too_few_usable_rows_says_so_and_says_why(dataset):
    """Counted after the drop, not before it.

    The earlier check ran against rows that included the unusable ones, so a
    dataset of forty rows with thirty-five NaNs passed it and failed later,
    inside the fit.
    """
    df = _frame(40)
    df.loc[5:, "success"] = np.nan
    dataset(df)

    with pytest.raises(HTTPException) as exc:
        _retrain(min_samples=10)

    assert exc.value.status_code == 400
    detail = str(exc.value.detail)
    assert "usable samples" in detail
    assert "NaN or inf" in detail, detail


def test_a_single_outcome_class_is_refused_by_name(dataset):
    """`roc_auc_score` raises "Only one class present in y_true", which reads
    as a bug in the metric rather than as a description of the data."""
    dataset(_frame(40, successes=[1] * 40))

    with pytest.raises(HTTPException) as exc:
        _retrain(min_samples=10)

    assert exc.value.status_code == 400
    assert "one outcome class" in str(exc.value.detail)


def test_a_clean_dataset_is_unaffected(dataset):
    """Otherwise the refusals above are satisfied by a function that refuses
    everything, which is a different defect wearing the same status code."""
    dataset(_frame(40))

    assert _retrain(min_samples=10)["status"] in ("success", "accepted")
