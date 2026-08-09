"""What `load_time_series` actually returns, and what its docstring may claim.

Written for #122. The docstring promised a trend-based backward extension that
`e555560` had removed as a critical bug, and a `n >= 70` readiness threshold
that moved to `ensemble.py` and became 33. Neither claim had a test, which is
why both survived the commits that falsified them.

Two kinds of assertion live here on purpose:

* **behaviour** -- what the frame contains. These would catch the synthetic
  backfill coming back, or the interpolation going away.
* **prose** -- what the docstring is allowed to say. A docstring is the only
  artefact in this file that no other test can falsify, and it is the one that
  was wrong. The threshold check imports `LSTM_MIN_ROWS` rather than hard-coding
  33, so changing the constant fails this file until the prose is updated with
  it.

Nothing here asserts that interpolation is correct or desirable. It records
what the function does, including the parts a caller would not want.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.services.forecasting.data_loader import load_time_series
from app.services.forecasting.ensemble import LSTM_MIN_ROWS

DOC = load_time_series.__doc__


def _db(observations):
    """A session whose Evaluation query returns exactly these (date, value) pairs."""
    rows = [
        SimpleNamespace(
            created_at=f"{day} 12:00:00",
            total_score=value,
            co2_reduction=None,
            success_probability=None,
        )
        for day, value in observations
    ]
    session = MagicMock()
    session.query.return_value.order_by.return_value.all.return_value = rows
    return session


def _load(observations):
    return load_time_series(_db(observations), "score")


# ------------------------------------------------------------------ behaviour


def test_no_row_precedes_the_first_observation():
    """The removed backfill invented dates before min(observed); nothing may now.

    This is the assertion that would have caught the synthetic extension had it
    existed when `e555560` was written: it fabricated `days_needed` daily points
    starting at `df["ds"].min() - timedelta(days=days_needed)`.
    """
    observations = [("2026-03-10", 40.0), ("2026-03-14", 60.0)]
    out = _load(observations)

    first_observed = pd.Timestamp("2026-03-10")
    assert out["ds"].min() == first_observed
    assert (out["ds"] >= first_observed).all()
    # ...and nothing past the last observation either.
    assert out["ds"].max() == pd.Timestamp("2026-03-14")


def test_an_interior_gap_is_filled_with_the_linear_midpoint():
    """A day between two observations gets the straight-line value.

    The midpoint is chosen deliberately: the 3-day centred smoothing in step 3
    is the identity on a linear run of interior points (mean of x-d, x, x+d is
    x), so this pins interpolation rather than interpolation-plus-smoothing.
    """
    out = _load([("2026-01-01", 10.0), ("2026-01-05", 50.0)])
    by_day = dict(zip(out["ds"], out["y"]))

    assert by_day[pd.Timestamp("2026-01-03")] == pytest.approx(30.0)
    assert by_day[pd.Timestamp("2026-01-02")] == pytest.approx(20.0)
    assert by_day[pd.Timestamp("2026-01-04")] == pytest.approx(40.0)


def test_a_long_gap_manufactures_a_day_for_every_date_it_spans():
    """The 23-day production gap (#75), in miniature.

    Two observations 21 days apart come back as 22 rows: 20 of them are days on
    which nothing was recorded.
    """
    out = _load([("2026-06-01", 10.0), ("2026-06-22", 31.0)])

    assert len(out) == 22
    observed_days = {pd.Timestamp("2026-06-01"), pd.Timestamp("2026-06-22")}
    invented = [d for d in out["ds"] if d not in observed_days]
    assert len(invented) == 20

    # The count is a span, not evidence: two observations, 22 rows. This is the
    # number EnsembleForecaster.fit reads as `n_samples`.
    assert len(out) > 2


def test_the_output_carries_no_observed_or_interpolated_flag():
    """The current limitation, named.

    Two columns and nothing else, so no consumer can tell an observation from a
    value computed between two of them. Asserted as an exact column list: adding
    provenance is a deliberate decision (#122) and should break this test rather
    than slip in.
    """
    out = _load([("2026-01-01", 10.0), ("2026-01-10", 100.0)])

    assert list(out.columns) == ["ds", "y"]
    for suspect in ("observed", "interpolated", "is_observed", "source", "provenance"):
        assert suspect not in out.columns


def test_smoothing_also_rewrites_the_observed_values():
    """Not only are invented rows unmarked -- the real ones are not returned intact.

    An observed spike of 100 between two 10s comes back as 40. Recorded because
    it compounds the flag problem: a caller cannot recover the measurements even
    if it knew which days were real.
    """
    out = _load([("2026-01-01", 10.0), ("2026-01-02", 100.0), ("2026-01-03", 10.0)])
    by_day = dict(zip(out["ds"], out["y"]))

    assert by_day[pd.Timestamp("2026-01-02")] == pytest.approx(40.0)
    assert by_day[pd.Timestamp("2026-01-02")] != 100.0


def test_same_day_evaluations_collapse_to_their_mean():
    out = _load([("2026-01-01", 10.0), ("2026-01-01", 30.0), ("2026-01-02", 20.0)])
    assert len(out) == 2
    assert out["y"].iloc[0] == pytest.approx(20.0)


def test_no_rows_yields_an_empty_frame_with_the_same_columns():
    session = MagicMock()
    session.query.return_value.order_by.return_value.all.return_value = []
    out = load_time_series(session, "score")

    assert list(out.columns) == ["ds", "y"]
    assert len(out) == 0


# ---------------------------------------------------------------------- prose


def test_the_docstring_does_not_promise_the_removed_backfill():
    """`e555560` deleted it; the prose kept advertising it for two commits."""
    lowered = DOC.lower()
    for claim in ("extending backward with trend-based synthetic data",
                  "trend-based synthetic"):
        assert claim not in lowered, f"docstring still promises: {claim!r}"

    # It may *describe* the removal -- that is the point of the history note --
    # but not as something the function still does.
    assert "no backward extension" in lowered


def test_the_docstring_does_not_restate_the_old_seventy_threshold():
    lowered = DOC.lower()
    for stale in ("requires n >= 70", "n >= 70 days", "if n < 70"):
        assert stale not in lowered, f"docstring still states: {stale!r}"


def test_the_docstring_agrees_with_the_ensemble_threshold():
    """The link #122 asks for: change LSTM_MIN_ROWS and this file goes red.

    Imported rather than hard-coded, so the test tracks the constant instead of
    pinning a second copy of it that could drift the same way the 70 did.
    """
    assert str(LSTM_MIN_ROWS) in DOC, (
        f"docstring must name the current readiness threshold ({LSTM_MIN_ROWS})"
    )
    assert "ensemble.py" in DOC, "and say which module owns it"


def test_the_docstring_states_that_interpolated_rows_are_unmarked():
    lowered = DOC.lower()
    assert "not marked" in lowered or "unmarked" in lowered
    assert "interpolat" in lowered


def test_the_docstring_does_not_call_interpolation_evidence_of_sufficiency():
    """The specific sentence that was there: "This enables LSTM training".

    Interpolation makes a series longer, not better attested, and the docstring
    must not offer it as a reason the data are adequate.
    """
    lowered = DOC.lower()
    assert "this enables lstm training" not in lowered
    assert "does not add information" in lowered
