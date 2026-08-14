"""A row entering the training set records when it did. The old ones cannot.

#183 established that the "temporal split" sliced row order, because there was
no time per row to split on. This writes one -- from here forward only.

The half that matters is the half that stays empty. All 17,071 rows already in
`projects.csv` predate this and there is no record of when each arrived.
Inventing a value would be the retroactive provenance #164 exists to undo, so
the column is NaN for them, permanently.

Which means a temporal split over this column selects the rows written after
today. That is a population, not a period, and anyone computing one has to
check how much of the column is filled first. Stated here because the failure
would look like a working split.
"""
import pandas as pd
import pytest


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

    # History, written before this feature existed: no recorded_at column.
    pd.DataFrame({
        "budget": [100000.0] * 12,
        "co2_reduction": [150.0] * 12,
        "social_impact": [7.0] * 12,
        "duration_months": [24.0] * 12,
        "success": [i % 2 for i in range(12)],
    }).to_csv(path, index=False)
    return path


def _read(path):
    return pd.read_csv(path)


def test_a_row_added_by_refresh_carries_the_moment(dataset):
    import app.api.retrain as retrain

    retrain.data_refresh(budget=1.0, co2_reduction=1.0, social_impact=1.0,
                         duration_months=1.0, success=1,
                         auto_retrain_threshold=10**9, current_user=None)

    df = _read(dataset)
    assert retrain.RECORDED_AT in df.columns
    assert pd.notna(df[retrain.RECORDED_AT].iloc[-1])


def test_the_rows_that_predate_it_stay_empty(dataset):
    """Not backfilled with today. A time invented for a row nobody timed is the
    retroactive provenance #164 exists to undo."""
    import app.api.retrain as retrain

    retrain.data_refresh(budget=1.0, co2_reduction=1.0, social_impact=1.0,
                         duration_months=1.0, success=1,
                         auto_retrain_threshold=10**9, current_user=None)

    df = _read(dataset)
    assert df[retrain.RECORDED_AT].isna().sum() == 12
    assert df[retrain.RECORDED_AT].notna().sum() == 1


def test_a_bulk_upload_stamps_only_what_it_adds(dataset):
    import io

    import app.api.retrain as retrain

    csv = ("budget,co2_reduction,social_impact,duration_months,success\n"
           "100000,150,7,24,1\n100000,150,7,24,0\n")

    class _Upload:
        filename = "u.csv"
        file = io.BytesIO(csv.encode())

    retrain.data_bulk_upload_content(file=_Upload(), auto_retrain=False,
                                     _admin=None)

    df = _read(dataset)
    assert df[retrain.RECORDED_AT].notna().sum() == 2
    assert df[retrain.RECORDED_AT].isna().sum() == 12


def test_it_is_not_a_feature(dataset):
    """It describes when a row was filed, not the project. Training on it would
    let the model learn the upload schedule."""
    import app.api.retrain as retrain

    with open(retrain.__file__, encoding="utf-8") as fh:
        block = fh.read()
    block = block[block.index("feature_cols = ["):]
    block = block[:block.index("]")]

    assert retrain.RECORDED_AT not in block
