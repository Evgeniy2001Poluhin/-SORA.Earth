"""The §7 gate must refuse a series that satisfies every count and holds nothing.

The protocol wrote this hole down itself (§10.2): twelve non-overlapping windows
at >=80% coverage is a test of record count, and repeated writes of a constant
pass it. Measured on the observation layer at the time -- 1.00 distinct values
per region across eight days, all five rosstat metrics and the sber baseline.

So the cases here are the four shapes that look like new information and are
not, plus the one that is:

    a constant, twelve windows deep                        refused
    a constant with a fresh ingested_at each day           refused
    a constant restated under new revisions                refused
    interpolated points filling the gaps                   not observations
    genuine periods with a changing value                  admitted

and the case that guards all of them: an empty candidate cannot pass, because
every condition is vacuously true over an empty set.
"""
from datetime import date, timedelta

import pytest

from app.services.forecasting.entry_conditions import (
    MIN_COVERAGE,
    Observation,
    REQUIRED_WINDOWS,
    evaluate,
    movements,
    real_observations,
)

REGIONS = ["RU-MOW", "RU-SPE"]
LAST_DAY = date(2027, 1, 20)
HORIZON = 7
# Twelve windows of seven days, plus the 90-day training window, plus slack.
SPAN_DAYS = 90 + REQUIRED_WINDOWS * HORIZON + 10
FIRST_DAY = LAST_DAY - timedelta(days=SPAN_DAYS)


def _daily(value_for, *, revision_for=None, interpolated=False, regions=REGIONS):
    """One observation per region per day, valued by `value_for(region, day)`."""
    out = []
    for region in regions:
        for offset in range(SPAN_DAYS + 1):
            day = FIRST_DAY + timedelta(days=offset)
            out.append(Observation(
                region=region,
                period_start=day,
                period_end=day,
                value=value_for(region, offset),
                revision=None if revision_for is None else revision_for(region, offset),
                interpolated=interpolated,
            ))
    return out


def _verdict(observations):
    return evaluate(observations, declared_regions=REGIONS,
                    horizon=HORIZON, last_day=LAST_DAY)


def _failed(verdict, fragment):
    return any(fragment in c.name for c in verdict.failures())


# --- the shapes that must be refused ----------------------------------------


def test_a_constant_series_is_refused_however_long_it_is():
    """The measured state of the platform, projected forward.

    Every counting condition passes -- the coverage is perfect and the windows
    are full. Only movement fails, which is the whole point of v1.1.
    """
    verdict = _verdict(_daily(lambda region, offset: 71.4))

    assert not verdict.passed
    assert _failed(verdict, "moves between source periods")
    # And it is *only* movement that fails: if coverage or the window count were
    # also failing, this case would not be demonstrating what it claims.
    assert [c.name for c in verdict.failures()] == [
        "the target moves between source periods, in every window"
    ], verdict.as_text()


def test_a_fresh_ingested_at_each_day_does_not_count_as_movement():
    """Writing the same period again with a new timestamp adds nothing.

    The gate never sees ingestion time -- it is not on Observation at all, which
    is the structural version of this guarantee. The behavioural version is
    that repeating a period collapses.
    """
    constant = _daily(lambda region, offset: 71.4)
    rewritten = constant + [
        Observation(region=o.region, period_start=o.period_start,
                    period_end=o.period_end, value=o.value)
        for o in constant
    ]

    assert len(real_observations(rewritten)) == len(real_observations(constant))
    assert not _verdict(rewritten).passed


def test_new_revisions_of_an_unchanged_value_are_not_movement():
    """A restatement is the same observation said twice.

    #121 gives every literal snapshot a content revision, so a source that
    republishes unchanged data still produces new revisions. That is provenance,
    not information.
    """
    verdict = _verdict(_daily(
        lambda region, offset: 71.4,
        revision_for=lambda region, offset: f"rev:v1:{offset:064d}",
    ))

    assert not verdict.passed
    assert _failed(verdict, "moves between source periods")


def test_interpolated_points_are_not_observations():
    """#132: resampling fills every calendar day, so a date being present stops
    meaning something was observed. A series made entirely of fill is empty."""
    filled = _daily(lambda region, offset: float(offset), interpolated=True)

    assert real_observations(filled) == []
    verdict = _verdict(filled)
    assert not verdict.passed
    assert _failed(verdict, "has observations at all")


def test_interpolation_cannot_manufacture_movement():
    """A constant, with interpolated points between the real ones that do move.

    The fill is what varies; the source does not. Dropping the interpolated
    points has to leave the constant behind.
    """
    real = _daily(lambda region, offset: 71.4)
    fake = [
        Observation(region=o.region, period_start=o.period_start,
                    period_end=o.period_end, value=o.value + offset,
                    interpolated=True)
        for offset, o in enumerate(real)
    ]

    verdict = _verdict(real + fake)

    assert not verdict.passed
    assert _failed(verdict, "moves between source periods")


def test_an_empty_candidate_cannot_pass():
    """Every condition is vacuously true over an empty set.

    Stated as its own condition rather than left to the others, because that is
    the oldest way a gate opens on nothing.
    """
    verdict = _verdict([])

    assert not verdict.passed
    assert _failed(verdict, "has observations at all")


def test_one_region_standing_still_refuses_the_whole_gate():
    """§7 is per declared region: all of them, every window."""
    moving = _daily(lambda region, offset: float(offset) if region == "RU-MOW" else 71.4)

    verdict = _verdict(moving)

    assert not verdict.passed
    assert "RU-SPE" in next(
        c.detail for c in verdict.failures() if "moves" in c.name)


# --- the shape that must be admitted ----------------------------------------


def test_a_genuinely_varying_target_passes():
    """Otherwise the refusals above would be satisfied by a gate that refuses
    everything, which proves nothing about what it is checking."""
    verdict = _verdict(_daily(lambda region, offset: 50.0 + offset * 0.37))

    assert verdict.passed, verdict.as_text()


def test_a_target_moving_once_per_window_is_enough():
    """The condition is movement, not a magnitude or a rate.

    A threshold on how much it must move would be a tuned parameter smuggled
    into a pre-registration.
    """
    verdict = _verdict(_daily(
        lambda region, offset: 50.0 + (offset // HORIZON)))

    assert verdict.passed, verdict.as_text()


def test_a_target_slower_than_the_horizon_is_refused():
    """The real constraint this expresses: a quantity published quarterly does
    not move inside a 7-day window, and accumulating history does not help."""
    verdict = _verdict(_daily(
        lambda region, offset: 50.0 + (offset // 90)))

    assert not verdict.passed
    assert _failed(verdict, "moves between source periods")


# --- the counting conditions still bite -------------------------------------


def test_thin_coverage_is_still_refused():
    """v1.1 adds a condition; it does not relax the six that were there."""
    sparse = [
        o for o in _daily(lambda region, offset: 50.0 + offset * 0.37)
        if o.period_end.toordinal() % 2 == 0
    ]

    verdict = _verdict(sparse)

    assert not verdict.passed
    assert _failed(verdict, "coverage")


def test_a_missing_declared_region_is_still_refused():
    verdict = evaluate(
        _daily(lambda region, offset: 50.0 + offset * 0.37, regions=["RU-MOW"]),
        declared_regions=REGIONS, horizon=HORIZON, last_day=LAST_DAY)

    assert not verdict.passed
    assert _failed(verdict, "declared region present")


def test_too_little_history_is_still_refused():
    recent = [
        o for o in _daily(lambda region, offset: 50.0 + offset * 0.37)
        if o.period_end > LAST_DAY - timedelta(days=REQUIRED_WINDOWS * HORIZON + 5)
    ]

    verdict = _verdict(recent)

    assert not verdict.passed
    assert _failed(verdict, "training window")


# --- the helpers ------------------------------------------------------------


def test_movements_counts_changes_not_rows():
    day = date(2026, 1, 1)
    same = [Observation("RU-MOW", day + timedelta(days=i),
                        day + timedelta(days=i), 5.0) for i in range(10)]

    assert movements(same) == 0
    assert movements(same[:5] + [
        Observation("RU-MOW", day + timedelta(days=5),
                    day + timedelta(days=5), 6.0)]) == 1


def test_the_declared_coverage_floor_is_the_protocol_number():
    """A silent relaxation of the floor would open the gate without an
    amendment, which §9 exists to prevent."""
    assert MIN_COVERAGE == 0.80
    assert REQUIRED_WINDOWS == 12
