"""The M3 coverage gauges count from the clock, not from thirty days back.

The gauges read `coverage_report` over a rolling 30 days. On 2026-10-20 that
window reached back to 2026-09-20 and reported 21 gaps, all on 2026-10-10: the
restored deployment's first day, four hours of 24. That day precedes the §7
clock (docs/M3_FORECAST_DECLARATION.md, Amendment 1.4), so it is not a day the
gate counts, and the gauge would keep reporting it until the window passed it.

The window is therefore [max(today - days, M3_CLOCK_START), today). These tests
cover the pure window arithmetic and the scheduler's use of it. The SQL is
PostgreSQL's and is covered in tests/test_observation_coverage_postgres.py.
"""
from datetime import date

import pytest

from app.schemas import ObservationCoverage
from app.services.observation_coverage import M3_CLOCK_START, coverage_window

TODAY = date(2026, 10, 20)


def test_the_clock_start_is_the_one_the_roadmap_test_holds():
    """Two copies of one fact, held equal rather than trusted to stay so."""
    from tests.test_development_roadmap_dates import CLOCK_START

    assert M3_CLOCK_START == CLOCK_START == date(2026, 10, 11)


def test_the_clock_bound_wins_while_the_rolling_bound_is_earlier():
    """Thirty days back from 2026-10-20 is 2026-09-20, before the clock."""
    assert coverage_window(30, M3_CLOCK_START, TODAY) == (date(2026, 10, 11), TODAY)


def test_without_a_clock_bound_the_window_is_the_rolling_one_the_api_serves():
    assert coverage_window(30, None, TODAY) == (date(2026, 9, 20), TODAY)


def test_after_thirty_days_the_rolling_bound_wins_over_the_clock():
    """From 2026-11-20 the rolling bound is 2026-10-21, later than the clock."""
    later = date(2026, 11, 20)

    assert coverage_window(30, M3_CLOCK_START, later) == (date(2026, 10, 21), later)


def test_today_is_the_exclusive_upper_bound():
    _, upper = coverage_window(30, M3_CLOCK_START, TODAY)

    assert upper == TODAY


def _fake_report(calls, *, gap_count=3, days_examined=16, points_examined=21):
    def fake(**kwargs):
        calls.append(kwargs)
        return ObservationCoverage(
            source=kwargs["source"], indicator=kwargs["indicator"],
            required_per_day=19, days_examined=days_examined,
            points_examined=points_examined, complete_days=days_examined - gap_count,
            gap_count=gap_count, gaps=[],
        )
    return fake


def test_the_scheduler_passes_the_clock_start_as_the_lower_bound(monkeypatch):
    """Red until the job passes `not_before`.

    Without the bound the job reads the rolling window, and the gauges report the
    2026-10-10 day that precedes the clock.
    """
    import app.scheduler as scheduler_module
    import app.services.observation_coverage as coverage

    calls = []
    monkeypatch.setattr(coverage, "coverage_report", _fake_report(calls))

    result = scheduler_module.scheduled_observation_coverage()

    assert len(calls) == 1, f"the job called coverage_report {len(calls)} times"
    assert calls[0].get("not_before") == M3_CLOCK_START, (
        f"the job passed not_before={calls[0].get('not_before')!r}; the gauges "
        f"then count days from before the M3 clock started"
    )
    assert calls[0]["days"] == 30
    assert result["status"] == "ok"


def test_the_three_gauges_are_set_from_one_report(monkeypatch):
    """One call, three values, all from that call.

    If the gauges were read from separate calls, a day could enter one reading
    and not another, and the denominator would not describe the count beside it.
    """
    import app.scheduler as scheduler_module
    import app.services.observation_coverage as coverage

    calls = []
    monkeypatch.setattr(coverage, "coverage_report", _fake_report(
        calls, gap_count=2, days_examined=9, points_examined=21))

    published = {}

    class _Recorder:
        def __init__(self, key):
            self.key = key

        def labels(self, **kwargs):
            return self

        def set(self, value):
            published[self.key] = value

    for name in ("sora_observation_coverage_gap_days",
                 "sora_observation_coverage_days_examined",
                 "sora_observation_coverage_points_examined"):
        monkeypatch.setattr(scheduler_module, name, _Recorder(name))

    scheduler_module.scheduled_observation_coverage()

    assert len(calls) == 1
    assert published == {
        "sora_observation_coverage_gap_days": 2,
        "sora_observation_coverage_days_examined": 9,
        "sora_observation_coverage_points_examined": 21,
    }


@pytest.mark.parametrize("gap_count", [0, 1])
def test_the_gauges_publish_the_report_they_were_given(monkeypatch, gap_count):
    import app.scheduler as scheduler_module
    import app.services.observation_coverage as coverage

    monkeypatch.setattr(coverage, "coverage_report", _fake_report(
        [], gap_count=gap_count))

    result = scheduler_module.scheduled_observation_coverage()

    assert result["gap_count"] == gap_count
    assert result["days_examined"] == 16
    assert result["points_examined"] == 21
