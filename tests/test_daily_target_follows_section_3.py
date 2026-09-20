"""§3 of the M3 declaration, executable.

The declaration fixed how a day is formed before any accumulation counted:

    target(point, day) = mean of the hourly values of that UTC day, if at least
                         19 of the 24 hours are present; otherwise the day is
                         absent

and Amendment 1.3 settled the ambiguity the data exposed -- "observations" there
means **distinct UTC hours**, not rows. The ingester stamps rows from
Open-Meteo's `current` block at 15-minute resolution, so one hour can hold
several, and every deployment adds one because `auto_openmeteo_ingestion` runs
at startup. Counting rows, three of the first sixteen days of the restarted
clock passed while short in hours.

Until now §3 existed only as prose. Nothing built the series it describes, so
the rule could not be wrong in code -- and could not be right either.

What these tests pin is the arithmetic, at the two places it can quietly differ
from the prose: the weight of a repeated hour, and the meaning of the floor.
"""
from datetime import date, datetime, timezone

import pytest

from app.services.forecasting.daily_target import daily_observations
from app.services.forecasting.entry_conditions import MIN_COVERAGE


def _rows(point, day, hours, value=10.0, minute=0):
    """One row per named hour, all with the same value unless overridden."""
    return [
        (point, datetime(day.year, day.month, day.day, h, minute,
                         tzinfo=timezone.utc), value)
        for h in hours
    ]


DAY = date(2026, 9, 3)


def test_a_full_day_becomes_one_observation():
    out = daily_observations(_rows("DEU", DAY, range(24)))

    assert len(out) == 1
    obs = out[0]
    assert obs.region == "DEU"
    assert obs.value == pytest.approx(10.0)


def test_the_period_is_the_day_itself():
    """`period_end` is the axis the §7 gate orders on, and a daily target's
    period starts and ends on the same date. A start left at the epoch, or at
    the first observation's timestamp, would make the windows wrong in a way
    nothing downstream could detect."""
    obs = daily_observations(_rows("DEU", DAY, range(24)))[0]

    assert obs.period_start == DAY
    assert obs.period_end == DAY


def test_a_repeated_hour_does_not_weigh_double():
    """The defect Amendment 1.3 exists for, in the arithmetic rather than in the
    coverage count.

    Hour 0 is sampled twice at 30 °C and every other hour once at 0 °C. By rows
    the mean is 30*2/25 = 2.4; by hours it is 30/24 = 1.25. Measured on
    production, the two readings of the daily target differ by up to 0.665 °C,
    and by exactly 0.000 on days with one sample per hour -- so the difference
    is made by how often the scheduler restarted that day, which is what §3's
    own rationale forbids.
    """
    rows = _rows("DEU", DAY, range(1, 24), value=0.0)
    rows += _rows("DEU", DAY, [0], value=30.0, minute=0)
    rows += _rows("DEU", DAY, [0], value=30.0, minute=30)

    obs = daily_observations(rows)[0]

    assert obs.value == pytest.approx(30.0 / 24), (
        "the repeated hour was counted twice: this is the row mean, not the "
        "hour mean, and it moves with the deployment schedule"
    )


def test_a_day_short_in_hours_is_absent_however_many_rows_it_has():
    """Twenty rows, eighteen hours. By rows it clears 19; by hours it does not,
    and six hours of that day were never observed."""
    rows = _rows("DEU", DAY, range(18))
    rows += _rows("DEU", DAY, [0], minute=15)
    rows += _rows("DEU", DAY, [1], minute=15)

    assert daily_observations(rows) == [], (
        "a day covering 18 of 24 hours produced a target value; §3 says it is "
        "absent, and an absent day is one fewer window the gate can use"
    )


def test_the_floor_is_the_gate_constant_not_a_second_number():
    """§3 says "80% is MIN_COVERAGE, the constant already declared in
    entry_conditions -- one number rather than two that could drift apart"."""
    floor = int(24 * MIN_COVERAGE)
    assert floor == 19

    assert daily_observations(_rows("DEU", DAY, range(floor))), (
        f"{floor} hours is the declared floor and was refused"
    )
    assert daily_observations(_rows("DEU", DAY, range(floor - 1))) == [], (
        f"{floor - 1} hours is below the declared floor and was accepted"
    )


def test_an_absent_day_is_absent_rather_than_interpolated():
    """§7.1 lists an interpolated point among the things that do not count as
    movement, and §3 says an absent day is not filled. A gap must arrive at the
    gate as a gap."""
    rows = _rows("DEU", date(2026, 9, 1), range(24))
    rows += _rows("DEU", date(2026, 9, 3), range(24))  # 09-02 missing entirely

    days = sorted(o.period_end for o in daily_observations(rows))

    assert days == [date(2026, 9, 1), date(2026, 9, 3)], (
        "the missing day was filled in; resampling makes 'a date is present' "
        "stop meaning 'something was observed' (#132)"
    )


def test_points_are_kept_apart():
    """One series per declared point. Pooling them would average a target
    across twenty-one places and call it one number."""
    rows = _rows("DEU", DAY, range(24), value=1.0)
    rows += _rows("RU-MOW", DAY, range(24), value=5.0)

    by_point = {o.region: o.value for o in daily_observations(rows)}

    assert by_point == {"DEU": pytest.approx(1.0), "RU-MOW": pytest.approx(5.0)}


def test_days_are_utc_days():
    """The declaration says UTC calendar day. A local-time boundary would move
    every day's membership by the host's timezone, silently."""
    rows = [
        ("DEU", datetime(2026, 9, 3, h, 0, tzinfo=timezone.utc), 10.0)
        for h in range(24)
    ]
    rows.append(("DEU", datetime(2026, 9, 4, 0, 0, tzinfo=timezone.utc), 99.0))

    out = {o.period_end: o.value for o in daily_observations(rows)}

    assert out[date(2026, 9, 3)] == pytest.approx(10.0), (
        "the 00:00 sample of the next UTC day was folded into this one"
    )
