"""The auto-weighting table `EnsembleForecaster` actually implements.

Written for #115, where the class docstring described a different table: three
tiers with a cutoff at 70, and an `LSTM=0.3, Prophet=0.7` pair. Both were once
true -- at 5ef1386 `LSTM_MIN_ROWS` was 70 and that pair was the middle tier --
and the code moved on without the prose. Nothing failed, because nothing tested
the tiers.

These tests are deliberately not the ones in `test_forecasting_ensemble.py`.
Those fit real Prophet and LSTM models, so they answer "did a model train here",
and they skip when one is unavailable. The tier a sample size selects is a
decision made before any model is touched, so it is tested with every sub-model
stubbed out: no training, no environment dependency, and a failure means the
table changed rather than that Prophet is missing.

Stubbing every `fit` to succeed also makes the final weights equal the branch
values exactly -- each tier already sums to 1.0, so the closing
`_normalize_weights()` is the identity. Anything else these assertions see is a
real change.
"""
import numpy as np
import pandas as pd
import pytest

from app.services.forecasting.ensemble import EnsembleForecaster, LSTM_MIN_ROWS


def _series(n: int) -> pd.DataFrame:
    """n daily observations with a mild trend -- shape only, never trained on."""
    return pd.DataFrame({
        "ds": pd.date_range("2026-01-01", periods=n, freq="D"),
        "y": np.linspace(60.0, 80.0, n),
    })


@pytest.fixture
def stub_submodels(monkeypatch):
    """Every sub-model fits successfully and records what it was given.

    The weighting branch runs before any of these are called, so replacing them
    isolates the decision under test from whether Prophet or torch can train.
    """
    seen: dict[str, pd.DataFrame] = {}

    def _record(name):
        def _fit(self, df, target_col, **kwargs):
            seen[name] = df.copy()
        return _fit

    from app.services.forecasting.ensemble import (
        LSTMForecaster, ProphetForecaster, LinearTrendForecaster,
    )
    monkeypatch.setattr(LSTMForecaster, "fit", _record("lstm"))
    monkeypatch.setattr(ProphetForecaster, "fit", _record("prophet"))
    monkeypatch.setattr(LinearTrendForecaster, "fit", _record("linear"))
    return seen


def _weights_for(n: int) -> dict:
    forecaster = EnsembleForecaster()
    forecaster.fit(_series(n), target_col="y")
    return forecaster.weights


# --------------------------------------------------------------- the real table


@pytest.mark.parametrize("n,expected", [
    # cold start -- the only tier that augments, see the bootstrap test below
    (5,   {"lstm": 0.0, "prophet": 0.6, "linear": 0.4}),
    (20,  {"lstm": 0.0, "prophet": 0.9, "linear": 0.1}),
    (40,  {"lstm": 0.2, "prophet": 0.8, "linear": 0.0}),
    (60,  {"lstm": 0.4, "prophet": 0.6, "linear": 0.0}),
    (150, {"lstm": 0.7, "prophet": 0.3, "linear": 0.0}),
])
def test_each_tier_assigns_its_documented_weights(n, expected, stub_submodels):
    assert _weights_for(n) == pytest.approx(expected)


@pytest.mark.parametrize("n_below,n_at", [
    (9, 10),                            # cold start -> normal
    (LSTM_MIN_ROWS - 1, LSTM_MIN_ROWS),  # 32 -> 33, LSTM becomes viable
    (49, 50),                           # -> moderate sample
    (99, 100),                          # -> LSTM dominant
])
def test_the_boundaries_are_where_the_constants_say(n_below, n_at, stub_submodels):
    """Each cutoff is `>=`, so n_at belongs to the upper tier and n_below does not.

    Boundaries get their own case because an off-by-one here is invisible in the
    tier test above -- both sides would still return *a* valid weight set.
    """
    assert _weights_for(n_below) != _weights_for(n_at)


def test_lstm_activates_at_33_not_70(stub_submodels):
    """The specific claim #115 was filed about.

    The docstring said LSTM is skipped below 70. Anyone sizing a dataset against
    that would provision 70 rows to get LSTM and actually get it at 33, in a
    0.2-weighted configuration the docstring did not mention.
    """
    assert _weights_for(33)["lstm"] == pytest.approx(0.2)
    assert _weights_for(69)["lstm"] > 0.0, "LSTM is active well below 70"
    assert _weights_for(32)["lstm"] == 0.0


def test_no_tier_produces_the_documented_lstm_03_prophet_07_pair(stub_submodels):
    """`LSTM=0.3, Prophet=0.7` was the middle tier at 5ef1386 and is now absent.

    It was split into 0.2/0.8 and 0.4/0.6 when the 50 tier arrived (e555560).
    Pinned as an absence so that reintroducing it has to be deliberate.
    """
    for n in (10, 20, 33, 40, 49, 50, 60, 99, 100, 150):
        w = _weights_for(n)
        assert not (w["lstm"] == pytest.approx(0.3) and w["prophet"] == pytest.approx(0.7)), (
            f"n={n} produced the 0.3/0.7 pair the docstring used to describe"
        )


def test_there_are_four_normal_tiers_plus_cold_start(stub_submodels):
    """Five distinct weight sets, not the three the docstring described."""
    distinct = {tuple(sorted(_weights_for(n).items())) for n in (5, 20, 40, 60, 150)}
    assert len(distinct) == 5


# ------------------------------------------- the two behaviours prose omitted


def test_below_ten_prophet_is_fitted_on_synthetic_rows(stub_submodels):
    """Cold start trains Prophet on `_bootstrap_augment(..., target_size=30)`.

    A metric computed here describes the augmenter as much as the model, which
    is the reason this is worth stating in the docstring rather than leaving in
    the body.
    """
    n = 6
    forecaster = EnsembleForecaster()
    forecaster.fit(_series(n), target_col="y")

    prophet_saw = stub_submodels["prophet"]
    assert len(prophet_saw) == 30, "Prophet is given the augmented frame, not the real one"
    assert len(prophet_saw) > n

    # LinearTrend, by contrast, gets the real rows.
    assert len(stub_submodels["linear"]) == n


def test_bootstrap_augmentation_does_not_run_outside_cold_start(stub_submodels):
    forecaster = EnsembleForecaster()
    forecaster.fit(_series(20), target_col="y")
    assert len(stub_submodels["prophet"]) == 20


def test_weights_are_redistributed_silently_when_a_member_fails(monkeypatch):
    """A failed member fit rewrites the weights and only logs.

    Two runs both reporting "the ensemble" need not have run the same
    combination, which is why it belongs in the docstring.
    """
    from app.services.forecasting.ensemble import (
        LSTMForecaster, ProphetForecaster, LinearTrendForecaster,
    )

    def _ok(self, df, target_col, **kwargs):
        return None

    def _boom(self, df, target_col, **kwargs):
        raise RuntimeError("LSTM unavailable")

    monkeypatch.setattr(LSTMForecaster, "fit", _boom)
    monkeypatch.setattr(ProphetForecaster, "fit", _ok)
    monkeypatch.setattr(LinearTrendForecaster, "fit", _ok)

    forecaster = EnsembleForecaster()
    forecaster.fit(_series(150), target_col="y")

    # Without the failure this tier is 0.7/0.3; the caller is told nothing.
    assert forecaster.weights["lstm"] == 0.0
    assert [name for name, _ in forecaster._active_models] == ["prophet", "linear"]

    # The inline comment in the LSTM failure branch says "Don't let Prophet reach 1.0 -
    # keep Linear as safety net". It does reach 1.0: linear carries 0.0 in every
    # normal tier, so normalising {0.0, 0.9, 0.0} puts everything on Prophet.
    # Pinned as measured rather than as intended -- the comment describes an
    # outcome the arithmetic does not produce.
    assert forecaster.weights["prophet"] == pytest.approx(1.0)
    assert forecaster.weights["linear"] == pytest.approx(0.0)


def test_a_prophet_failure_moves_the_weight_the_other_way(monkeypatch):
    from app.services.forecasting.ensemble import (
        LSTMForecaster, ProphetForecaster, LinearTrendForecaster,
    )

    monkeypatch.setattr(LSTMForecaster, "fit", lambda self, df, t, **k: None)
    monkeypatch.setattr(ProphetForecaster, "fit",
                        lambda self, df, t, **k: (_ for _ in ()).throw(RuntimeError("no prophet")))
    monkeypatch.setattr(LinearTrendForecaster, "fit", lambda self, df, t, **k: None)

    forecaster = EnsembleForecaster()
    forecaster.fit(_series(150), target_col="y")

    assert forecaster.weights["prophet"] == 0.0
    assert forecaster.weights["lstm"] == pytest.approx(1.0)


def _frame(n_rows):
    import pandas as pd
    return pd.DataFrame({
        "ds": pd.date_range("2026-01-01", periods=n_rows, freq="D"),
        "y": [50.0 + (i % 7) for i in range(n_rows)],
    })


def _insufficient_data(model, n_rows):
    """Did LSTMForecaster refuse this many rows as too few?

    Any other failure -- and tiny frames fail in several ways once training
    starts -- is not what this asks about.
    """
    try:
        model.fit(_frame(n_rows), "y", epochs=1)
    except ValueError as e:
        return "Insufficient data" in str(e)
    except Exception:
        return False
    return False


def test_the_gate_matches_what_the_lstm_actually_requires():
    """LSTM_MIN_ROWS is a literal; LSTMForecaster recomputes its own minimum.

    Nothing links them. Raising `seq_length` leaves this gate at 33 while the
    model needs more, so the ensemble admits LSTM, the fit raises, and the
    weights are redistributed silently -- through the gate meant to prevent
    exactly that.

    Driving the real model across the boundary rather than restating
    `seq_length + 14 + 5` is the point: a test that recomputes the formula
    agrees with itself no matter how far the two drift apart.
    """
    from app.services.forecasting.lstm import LSTMForecaster

    assert _insufficient_data(LSTMForecaster(), LSTM_MIN_ROWS - 1), (
        f"the LSTM accepts {LSTM_MIN_ROWS - 1} rows, so the ensemble's gate "
        f"of {LSTM_MIN_ROWS} is stricter than the model requires"
    )
    assert not _insufficient_data(LSTMForecaster(), LSTM_MIN_ROWS), (
        f"the LSTM refuses {LSTM_MIN_ROWS} rows as insufficient, but the "
        f"ensemble admits it at that size -- the fit will raise and the "
        f"weights will be redistributed silently"
    )
