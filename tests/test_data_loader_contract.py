"""What `load_time_series` returns, and what its docstring is allowed to claim.

Written for #122, where the docstring promised a trend-based backward extension
that had been removed, and a `n >= 70` readiness threshold that no longer lives
in this module. Neither claim had a test, which is why both survived the commits
that falsified them.

Two kinds of assertion, on purpose:

* **behaviour** -- what the frame contains. These catch the backfill returning,
  or the interpolation going away.
* **prose** -- the promises the docstring makes. A docstring is the only
  artefact here that no other test can falsify, and it is the one that was
  wrong. These check meaning, not wording: that the removed behaviour is not
  re-promised, and that the limits a caller must account for are stated.

The prose tests deliberately do **not** import anything from `ensemble`. Tying
this docstring to a constant in another module is how the stale `70` got here in
the first place -- the loader should say that readiness is ensemble policy, not
restate the current number and go stale again when it moves.

Nothing here asserts that interpolation or smoothing is correct or desirable. It
records what the function does, including the parts a caller would not want.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.services.forecasting.data_loader import load_time_series

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


def test_no_row_falls_outside_the_observed_range():
    """The removed backfill invented dates before min(observed); nothing may now."""
    out = _load([("2026-03-10", 40.0), ("2026-03-14", 60.0)])

    assert out["ds"].min() == pd.Timestamp("2026-03-10")
    assert out["ds"].max() == pd.Timestamp("2026-03-14")


def test_an_interior_gap_is_filled_with_the_linear_midpoint():
    """A day between two observations gets the straight-line value.

    Interior points are checked deliberately: the centred 3-day mean is the
    identity on a linear run (mean of x-d, x, x+d is x), so this pins
    interpolation rather than interpolation-plus-smoothing.
    """
    out = _load([("2026-01-01", 10.0), ("2026-01-05", 50.0)])
    by_day = dict(zip(out["ds"], out["y"]))

    assert by_day[pd.Timestamp("2026-01-02")] == pytest.approx(20.0)
    assert by_day[pd.Timestamp("2026-01-03")] == pytest.approx(30.0)
    assert by_day[pd.Timestamp("2026-01-04")] == pytest.approx(40.0)


def test_a_long_gap_manufactures_a_day_for_every_date_it_spans():
    """Two observations 21 days apart come back as 22 rows, 20 never recorded."""
    out = _load([("2026-06-01", 10.0), ("2026-06-22", 31.0)])

    assert len(out) == 22
    observed = {pd.Timestamp("2026-06-01"), pd.Timestamp("2026-06-22")}
    assert len([d for d in out["ds"] if d not in observed]) == 20


def test_an_observed_value_is_returned_as_measured():
    """The measurement comes back as itself.

    This asserted the opposite while a centred 3-day rolling mean was applied
    to the whole column: 10, 100, 10 came back as 55, 40, 55, so a measured
    peak of 100 was reported as 40. It was described as light smoothing to
    reduce noise, and measurement showed it could not be that -- on an
    interpolated stretch the values are already linear and a centred mean is
    the identity there, shift exactly 0.000000. The only rows it could change
    were the observed ones (#132).

    Kept as a test rather than deleted: what mattered then still matters, with
    the sign reversed.
    """
    out = _load([("2026-01-01", 10.0), ("2026-01-02", 100.0), ("2026-01-03", 10.0)])
    by_day = dict(zip(out["ds"], out["y"]))

    assert by_day[pd.Timestamp("2026-01-02")] == pytest.approx(100.0)
    assert by_day[pd.Timestamp("2026-01-01")] == pytest.approx(10.0)
    assert by_day[pd.Timestamp("2026-01-03")] == pytest.approx(10.0)


def test_same_day_evaluations_collapse_to_their_mean():
    """Daily aggregation in isolation: one date, several evaluations, no neighbours.

    A second day would defeat the point. With two days the smoothing step
    averages them, and if their daily means happen to be equal it returns the
    same number the aggregation did -- so the assertion would pass whether or
    not step 1 ran. A single date leaves one row, where the centred mean is the
    identity, and only the aggregation can produce the value.
    """
    out = _load([("2026-01-01", 10.0), ("2026-01-01", 30.0), ("2026-01-01", 50.0)])

    assert len(out) == 1
    assert out["y"].iloc[0] == pytest.approx(30.0)


def test_every_row_says_whether_it_was_observed():
    """The provenance the frame used to lack.

    It asserted the columns were exactly ds and y, so a consumer had no way to
    tell a measurement from a day the interpolation manufactured -- and
    therefore treated them alike, because it could not do otherwise. That is
    how a 14-observation series was presented to the ensemble as 51 samples.

    The flag is asserted against the dates that were actually supplied, not
    against a count: a column of all True would satisfy a count and answer
    nothing.
    """
    out = _load([("2026-01-01", 10.0), ("2026-01-10", 100.0)])

    assert list(out.columns) == ["ds", "y", "is_observed"]
    observed = {d.date().isoformat() for d in out[out["is_observed"]]["ds"]}
    assert observed == {"2026-01-01", "2026-01-10"}
    assert int(out["is_observed"].sum()) == 2
    assert len(out) == 10, "the span is still filled; only its labelling changed"


def test_an_empty_result_carries_the_provenance_column_too():
    """Both empty paths, asserted directly.

    The early returns produced ds and y while the populated path produced three
    columns, so a caller doing df["is_observed"] got a KeyError on the one
    input it is most likely to hit -- and the docstring was wrong about the
    case nobody looks at. The existing empty-frame test passed throughout,
    because it only checked ds and y.
    """
    no_rows = _load([])

    assert list(no_rows.columns) == ["ds", "y", "is_observed"]
    assert no_rows.empty

    session = MagicMock()
    session.query.return_value.order_by.return_value.all.return_value = [
        SimpleNamespace(created_at="2026-01-01 12:00:00", total_score=None,
                        co2_reduction=None, success_probability=None)
    ]
    no_values = load_time_series(session, "score")

    assert list(no_values.columns) == ["ds", "y", "is_observed"]
    assert no_values.empty


def test_no_rows_yields_an_empty_frame_with_the_same_columns():
    session = MagicMock()
    session.query.return_value.order_by.return_value.all.return_value = []
    out = load_time_series(session, "score")

    assert list(out.columns) == ["ds", "y", "is_observed"]
    assert len(out) == 0


def _db_with_no_values():
    """Evaluations exist, but every metric on them is NULL.

    All three columns are `Column(Float)` -- nullable -- so this is a state the
    database can actually hold, not a constructed impossibility.
    """
    session = MagicMock()
    session.query.return_value.order_by.return_value.all.return_value = [
        SimpleNamespace(created_at=f"2026-01-0{d} 12:00:00", total_score=None,
                        co2_reduction=None, success_probability=None)
        for d in (1, 2)
    ]
    return session


@pytest.mark.parametrize("metric", ["score", "co2_reduction", "prob"])
def test_rows_with_no_values_for_the_metric_keep_the_schema(metric):
    """The second empty path, which used to return a frame with no columns.

    `recs` is filtered by `is not None`, so rows-present/all-NULL collapses to
    the same empty list as no-rows -- but it reached `pd.DataFrame(recs)`
    instead of the early return, and a DataFrame built from an empty list has
    no columns at all. A caller doing `df["ds"]` got a KeyError rather than an
    empty series, and the docstring promised otherwise.

    Parametrised because the metric is dispatched by an if/elif/else with three
    separate comprehensions, not one attribute lookup: each branch is its own
    opportunity to reintroduce this.
    """
    out = load_time_series(_db_with_no_values(), metric)

    assert out.empty
    assert list(out.columns) == ["ds", "y", "is_observed"], (
        f"{metric}: empty result carries no schema, so downstream column "
        f"access raises instead of yielding nothing"
    )


def test_the_two_empty_paths_return_the_same_schema():
    """Whichever way the result is empty, it has to look the same.

    Asserted against each other rather than against a literal: a change that
    altered both paths together would otherwise satisfy two separate literal
    assertions while callers still saw the schema move.
    """
    no_rows = MagicMock()
    no_rows.query.return_value.order_by.return_value.all.return_value = []

    from_no_rows = load_time_series(no_rows, "score")
    from_no_values = load_time_series(_db_with_no_values(), "score")

    assert from_no_rows.empty and from_no_values.empty
    assert list(from_no_rows.columns) == list(from_no_values.columns)


# ---------------------------------------------------------------------- prose


def test_the_docstring_does_not_promise_a_backward_extension():
    """The removed behaviour must not be re-promised."""
    lowered = DOC.lower()

    for claim in ("trend-based synthetic", "extending backward",
                  "synthetic data", "backfill"):
        assert claim not in lowered, f"docstring promises removed behaviour: {claim!r}"

    assert "no backward" in lowered or "no row is produced outside" in lowered


def test_the_docstring_does_not_restate_the_old_threshold():
    lowered = DOC.lower()

    for stale in ("n >= 70", "70 days", "n < 70"):
        assert stale not in lowered, f"docstring restates the removed threshold: {stale!r}"


def test_the_docstring_leaves_readiness_to_the_ensemble():
    """It must name the owner without copying the number.

    Restating the current threshold is what went stale before; the loader points
    at the module that decides instead.
    """
    lowered = DOC.lower()

    assert "ensemble" in lowered
    assert "readiness is not decided here" in lowered
    assert "no threshold of its own" in lowered


def test_the_docstring_states_that_provenance_is_present():
    """It said the opposite, correctly, while there was no flag.

    The docstring had to warn that nothing marked observed rows apart. Now one
    does, and a docstring still carrying the warning would send a consumer past
    the column it should be reading -- which is how the old text kept being
    obeyed after the behaviour changed.
    """
    lowered = DOC.lower()

    assert "is_observed" in DOC, "the provenance column is not documented"
    assert "no provenance" not in lowered
    for stale in ("nothing marks which", "no flag for which"):
        assert stale not in lowered, f"the docstring still denies provenance: {stale!r}"


def test_the_docstring_does_not_promise_smoothing():
    """The rolling mean is gone, and the text must not keep offering it.

    Asserted as a refutation rather than as the absence of a word: the
    docstring mentions the removed step in order to say it was removed, and a
    plain substring ban would trip on the sentence doing the correcting -- a
    trap this repository has fallen into three times.
    """
    lowered = DOC.lower()

    assert "used to be applied" in lowered or "is now" in lowered, (
        "the docstring does not say the smoothing was removed"
    )
    assert "returned as measured" in lowered or "not the measurement taken" not in lowered


def test_the_docstring_states_that_length_is_a_span_not_evidence():
    """Interpolation makes the frame longer, not better attested."""
    lowered = DOC.lower()

    assert "calendar span" in lowered
    assert "not evidence" in lowered
    assert "this enables lstm training" not in lowered
