"""Synthetic weather pairs only; no model training, downloads or private data."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.services.forecasting import replay_calibration as calibration

ORIGIN = datetime(2025, 1, 31, 23, 59, 59, tzinfo=timezone.utc)


def pair(days_ago, *, lead=1, point="DEU", actual=12.0, forecast=10.0, origin=ORIGIN):
    return calibration.CalibrationPair(point, origin - timedelta(days=days_ago),
                                       lead, forecast, actual)


def history(count=180, *, lead=1, point="DEU", actual=12.0, forecast=10.0):
    return [pair(lead + 1 + i, lead=lead, point=point, actual=actual, forecast=forecast)
            for i in range(count)]


def fit(rows, **kwargs):
    return calibration.fit_correction(rows, **{"point_code": "DEU", "lead_day": 1,
                                              "origin": ORIGIN, **kwargs})


@pytest.mark.parametrize("count,expected", [(0, "raw_fallback_insufficient_history"),
                                          (179, "raw_fallback_insufficient_history"),
                                          (180, "calibrated")])
def test_exact_minimum_and_fallback(count, expected):
    result = fit(history(count))
    assert result.status == expected
    assert result.eligible_pairs == count
    assert result.correction_c == (2.0 if count >= 180 else 0.0)
    assert result.apply(20.0) == (22.0 if count >= 180 else 20.0)
    assert "not historical availability" in result.label
    assert "not live M3" in result.label


def test_negative_bias_sign_and_zero_bias_are_distinct_from_fallback():
    negative = fit(history(actual=2.0, forecast=10.0))
    assert negative.apply(20.0) == pytest.approx(12.0)
    zero = fit(history(actual=10.0))
    assert zero.status == "calibrated"
    assert zero.correction_c == 0


@pytest.mark.parametrize("lead", [1, 7])
def test_purges_overlapping_future_targets_even_when_issued_before_origin(lead):
    good = history(180, lead=lead)
    # Issue exactly lead days ago targets today, whose complete day ends after O.
    poison = pair(lead, lead=lead, actual=1_000_000)
    assert poison.issued_at < ORIGIN < poison.target_end
    result = fit([*good, poison], lead_day=lead)
    assert result.eligible_pairs == 180
    assert result.correction_c == pytest.approx(2)
    assert result.last_target_end <= ORIGIN


def test_same_origin_and_future_issues_cannot_enter_fit():
    result = fit([*history(), pair(0, actual=1_000_000), pair(-10, actual=1_000_000)])
    assert result.eligible_pairs == 180
    assert result.correction_c == pytest.approx(2)


def test_window_left_is_inclusive_and_just_older_is_excluded():
    rows = history(178)
    rows += [pair(365, actual=30), pair(366, actual=1_000_000)]
    result = fit(rows)
    assert result.status == "raw_fallback_insufficient_history"
    assert result.eligible_pairs == 179
    assert result.first_issue == ORIGIN - timedelta(days=365)


def test_leap_year_window_is_365_days_not_calendar_year():
    origin = datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    result = fit([pair(365, origin=origin), pair(366, origin=origin)], origin=origin)
    assert result.eligible_pairs == 1
    assert result.first_issue.isoformat() == "2024-01-01T23:59:59+00:00"


def test_target_day_is_whole_utc_day_and_leap_day_supported():
    issue = datetime(2024, 2, 28, 23, 59, 59, tzinfo=timezone.utc)
    row = calibration.CalibrationPair("DEU", issue, 1, 10, 12)
    assert row.target_day.isoformat() == "2024-02-29"
    assert row.target_end.isoformat() == "2024-03-01T00:00:00+00:00"
    leap_issue = replace(row, issued_at=issue + timedelta(days=1))
    assert leap_issue.target_day.isoformat() == "2024-03-01"
    assert leap_issue.target_end.isoformat() == "2024-03-02T00:00:00+00:00"


def test_lead_seven_requires_eight_midnight_steps_from_issue_date():
    row = pair(8, lead=7)
    assert row.target_day.isoformat() == "2025-01-30"
    assert row.target_end.isoformat() == "2025-01-31T00:00:00+00:00"
    assert fit([row], lead_day=7).eligible_pairs == 1
    assert fit([pair(7, lead=7)], lead_day=7).eligible_pairs == 0


def test_point_and_lead_are_separate_not_pooled():
    result = fit([*history(179), *history(180, point="KEN", actual=1_000_000),
                  *history(180, lead=7, actual=1_000_000)])
    assert result.eligible_pairs == 179
    assert result.status == "raw_fallback_insufficient_history"
    ken = fit(history(180, point="KEN", actual=15), point_code="KEN")
    assert ken.correction_c == pytest.approx(5)


@pytest.mark.parametrize("outside", [False, True])
def test_duplicates_refuse_even_outside_requested_cell(outside):
    row = pair(10, point="KEN" if outside else "DEU")
    with pytest.raises(calibration.CalibrationRefusal, match="duplicate"):
        fit([row, replace(row, actual_c=99)])


def test_selection_hash_and_sum_ignore_input_order_but_record_keys():
    rows = history()
    rows[0] = replace(rows[0], actual_c=13)
    result = fit(rows)
    assert fit(reversed(rows)) == result
    assert result.correction_c == pytest.approx(2 + 1 / 180)
    assert result.first_issue == rows[-1].issued_at
    assert result.last_issue == rows[0].issued_at
    assert result.selected_keys_sha256 != fit(rows[:-1]).selected_keys_sha256
    # Selection identity is explicitly not a hash of temperature values.
    assert result.selected_keys_sha256 == fit(history()).selected_keys_sha256


@pytest.mark.parametrize("value", [True, None, "12", float("inf"), float("nan"), 10**400])
def test_bad_numbers_refuse(value):
    with pytest.raises(calibration.CalibrationRefusal, match="finite"):
        pair(10, actual=value)


@pytest.mark.parametrize("lead", [True, 0, 8, 1.0, "1"])
def test_invalid_lead_refuses(lead):
    with pytest.raises(calibration.CalibrationRefusal, match="lead"):
        pair(10, lead=lead)


@pytest.mark.parametrize("origin", [ORIGIN.replace(tzinfo=None),
                                   ORIGIN.replace(tzinfo=timezone(timedelta(hours=3))),
                                   ORIGIN.replace(hour=0), ORIGIN.replace(microsecond=1)])
def test_cutoff_is_explicit_utc_and_exact(origin):
    with pytest.raises(calibration.CalibrationRefusal, match="cutoff"):
        fit([], origin=origin)


def test_invalid_point_and_unvalidated_cache_row_refuse():
    with pytest.raises(calibration.CalibrationRefusal, match="unknown point"):
        fit([], point_code="UNKNOWN")
    with pytest.raises(calibration.CalibrationRefusal, match="validated"):
        fit([{"point_code": "DEU"}])


def test_finite_operands_cannot_produce_infinite_residual_or_prediction():
    with pytest.raises(calibration.CalibrationRefusal, match="finite"):
        pair(10, forecast=-1e308, actual=1e308)
    result = fit(history(forecast=0, actual=1e308))
    with pytest.raises(calibration.CalibrationRefusal, match="finite"):
        result.apply(1e308)


def test_input_budget_bounds_generator_consumption(monkeypatch):
    monkeypatch.setattr(calibration, "MAX_INPUT_PAIRS", 2)
    with pytest.raises(calibration.CalibrationRefusal, match="budget"):
        fit(iter(history(3)))


def test_unsupported_calendar_limits_refuse():
    with pytest.raises(calibration.CalibrationRefusal, match="calendar"):
        pair(0, origin=datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc))
    with pytest.raises(calibration.CalibrationRefusal, match="window"):
        fit([], origin=datetime(1, 1, 1, 23, 59, 59, tzinfo=timezone.utc))
