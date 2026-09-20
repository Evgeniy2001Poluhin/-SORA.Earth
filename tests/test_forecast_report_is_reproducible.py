"""Phase 6's exit criterion: one reproducible report over one snapshot.

`docs/DEVELOPMENT_ROADMAP.md` phase 6 asks for naive, seasonal naive and moving
average; rolling-origin; MAE, MASE, bias, bootstrap CI and worst region -- and
then "**one reproducible report over one snapshot**; a baseline is mandatory".

Every component existed. Measured 2026-09-20, `app/services/forecasting/evaluation.py`
was imported by tests and by nothing else: no script, no endpoint, no job. So
the harness could not produce the report the phase is judged by, while reading
as done from the code.

What a report has to do beyond computing numbers:

- **refuse to be evidence before §7 says so.** The gate is executable; a report
  that omits its verdict is one someone will quote in February.
- **say what it was computed from.** A number without its window, its floor and
  its input is not reproducible, which is the word in the exit criterion.
- **name the bar.** A candidate does not exist yet for this target, so the
  useful content today is which baseline is hardest to beat and what it scores.
"""
from datetime import datetime, timedelta, timezone

import math
import random

import pytest

from app.services.forecasting.report import build_report


def _series(points=("DEU",), days=200, start=datetime(2026, 1, 1, tzinfo=timezone.utc)):
    """A full-coverage hourly series: weekly cycle, daily swing, and drift.

    The drift term is not decoration. Without it the series is *exactly*
    weekly-periodic, and MASE's denominator -- the mean absolute change of the
    seasonal-naive forecast over the training slice -- is zero. The harness then
    refuses every baseline with "seasonal scale is zero: the training slice is
    perfectly periodic", which is correct of it and useless as a test.

    The first version of this helper used a pure sine of period 7 and appeared
    to work: sine is periodic only to floating point, so the scale came out
    around 1e-15 rather than 0, MASE came out astronomically large, and the
    assertions passed on numerical noise. A deterministic pseudo-random walk
    keeps the series reproducible and the denominator real.
    """
    rng = random.Random(11)
    rows = []
    for point in points:
        level = 10.0
        for d in range(days):
            level += rng.uniform(-0.5, 0.5)
            day = start + timedelta(days=d)
            for hour in range(24):
                value = level + 5 * math.sin(d / 7 * 2 * math.pi) + hour / 24
                rows.append((point, day + timedelta(hours=hour), value))
    return rows


def test_the_report_states_what_it_was_computed_from():
    report = build_report(_series(), target="openmeteo:temperature")

    snapshot = report["snapshot"]
    assert snapshot["points"] == 1
    assert snapshot["days"] == 200
    assert snapshot["first_day"] and snapshot["last_day"]
    assert snapshot["required_hours_per_day"] == 19, (
        "the report does not state the coverage floor it applied, so two runs "
        "under different floors produce numbers that cannot be compared"
    )


def test_the_gate_verdict_travels_with_the_numbers():
    """§7 is executable and the report carries what it said.

    Not a formality: 200 days is enough for h=7 and not for h=30, and a reader
    who sees only the scores cannot tell which of the two they are looking at.
    """
    report = build_report(_series(days=200), target="openmeteo:temperature")

    assert "gate" in report
    assert set(report["gate"]) == {"7", "30"}
    for horizon, verdict in report["gate"].items():
        assert "passed" in verdict and "conditions" in verdict, horizon


def test_nothing_is_called_evidential_while_the_gate_refuses():
    """The one claim the report must never make by accident."""
    short = build_report(_series(days=40), target="openmeteo:temperature")

    assert short["evidential"] is False
    assert short["gate"]["7"]["passed"] is False
    assert short["why_not_evidential"], (
        "the report says it is not evidence and does not say why, which is the "
        "half a reader needs to know when it will be"
    )


def test_the_baselines_are_scored_over_identical_windows():
    """A mandatory baseline, per phase 6, and the bar named.

    Identical windows is the point: comparing one model scored on one split
    against another scored on a different split compares two arrangements.
    """
    report = build_report(_series(days=200), target="openmeteo:temperature")

    baselines = report["baselines"]["DEU"]
    assert baselines["scored"], "no baseline was scored on a 200-day series"
    assert baselines["hardest"] in baselines["scored"], (
        "the report names a hardest baseline that it did not score"
    )
    for name, figures in baselines["scored"].items():
        assert "mase" in figures and "mae" in figures, name
        assert "worst_window" in figures, (
            f"{name} reports a mean and not its worst window; phase 6 asks for "
            f"the worst region, and an average hides the window that fails"
        )


def test_a_series_too_short_to_score_says_so_rather_than_reporting_nothing():
    """An empty `scored` map means "no baseline won" or "no baseline ran", and
    those are different facts -- the same denominator problem the coverage
    endpoint has."""
    report = build_report(_series(days=20), target="openmeteo:temperature")

    baselines = report["baselines"]["DEU"]
    assert baselines["scored"] == {}
    assert baselines["skipped"], (
        "nothing was scored and nothing was recorded as skipped, so the report "
        "cannot distinguish a failure to run from a clean sheet"
    )


def test_two_runs_over_the_same_snapshot_agree():
    """Reproducible is the word in the exit criterion.

    The baselines are deterministic; the bootstrap confidence interval is not
    unless it is seeded. `evaluation.bootstrap_ci` takes a seed for exactly this
    reason, and a report that varies between runs over one snapshot cannot be
    the artefact phase 6 asks for.
    """
    rows = _series(days=200)

    first = build_report(rows, target="openmeteo:temperature")
    second = build_report(rows, target="openmeteo:temperature")

    del first["generated_at"], second["generated_at"]
    assert first == second, (
        "two runs over one snapshot disagreed; the report is not reproducible"
    )
