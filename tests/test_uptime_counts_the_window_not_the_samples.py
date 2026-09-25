"""Uptime is a fraction of the window, not a fraction of the samples that exist.

`GET /api/v1/status/uptime` is public and unauthenticated, and the page renders
each component's figure as "Uptime 24h". It was computed as

    rows = db.query(HealthPing.ok).filter(component == comp, ts >= since).all()
    return round(len([1 for (ok,) in rows if ok]) / len(rows) * 100, 2)

-- ok rows over *observed* rows. The only writer of `health_pings` is
`record_health()`, which runs inside the process being measured, so an outage
leaves **absence**, not `ok=False`, and absence never enters the denominator.

Measured 2026-09-25 against a 24-hour window at the scheduler's own 5-minute
cadence, with six hours holding no sample at all:

    expected samples in 24h @5min : 288
    samples actually present      : 216  (75% of the window)
    hours with no evidence        : 6
    uptime_24h reported           : 100.0

and the control, 72 samples present and saying not-ok: 75.0. So the reader was
not broken; the denominator was.

The sample is additionally conditioned on the system being up. `status_summary`
pings on every request and the page refreshes every 30s, so one open tab writes
120 of the 132 samples an hour -- 91% -- each taken at a moment the API was
answering.

What replaces it: the window is divided into slots one sample interval wide. A
slot counts as up only if it holds a sample and none of its samples says not-ok;
a slot with no sample is not up. That makes the figure a **lower bound**, which
is the convention this repository already applies to the promotion gate -- the
95% lower bound of AUC must clear the threshold, not the point estimate -- and
it needs no new threshold. `coverage_*` says how much of the window was observed.

The pair states a range without anybody choosing a cut-off -- the truth lies
between `uptime` and `uptime + (100 - coverage)` -- and that matters because not
every component has a sampler on a cadence. The scheduler container stopped
claiming `api`, which it cannot observe, so those rows now arrive only when
somebody opens the page: a bare lower bound would show a healthy API at 0.7%.
On a fully sampled window the two ends meet and the page prints one number.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

import pytest

from app.database import HealthPing, SessionLocal
from app.services.status_service import (
    HEALTH_PING_INTERVAL_MINUTES,
    _uptime,
    _window,
    status_summary,
)

COMP = "test_uptime_component"
WINDOW_H = 24
SLOT = HEALTH_PING_INTERVAL_MINUTES
TOTAL_SLOTS = WINDOW_H * 60 // SLOT


@pytest.fixture
def db():
    """A session with this test's own component, cleared before and after.

    A component name of its own rather than "api": other tests reach
    `status_summary`, which writes real rows, and a shared name would make the
    seeded window depend on what ran first.
    """
    session = SessionLocal()
    session.query(HealthPing).filter(HealthPing.component == COMP).delete()
    session.commit()
    try:
        yield session
    finally:
        session.query(HealthPing).filter(HealthPing.component == COMP).delete()
        session.commit()
        session.close()


def _seed(db, slots):
    """One sample in the middle of each named slot.

    `slots` maps a slot index -- 0 is the oldest in the window -- to the `ok`
    value written there. Placed mid-slot so the few milliseconds between this
    `utcnow()` and the one inside the reader cannot move a sample across a
    boundary.
    """
    since = datetime.utcnow() - timedelta(hours=WINDOW_H)
    for idx, ok in slots.items():
        db.add(HealthPing(
            component=COMP, ok=ok,
            ts=since + timedelta(minutes=SLOT * idx) + timedelta(minutes=SLOT / 2),
        ))
    db.commit()


def test_an_unrecorded_outage_is_not_uptime(db):
    """Six hours nothing could write. The old computation answered 100.0."""
    hole = set(range(100, 100 + 6 * 60 // SLOT))          # 72 consecutive slots
    _seed(db, {i: True for i in range(TOTAL_SLOTS) if i not in hole})

    result = _window(db, COMP, WINDOW_H)

    # The headline figure first: a computation that still reported 100 here
    # would otherwise fail on `coverage` and never say what was actually wrong.
    assert result["uptime"] == 75.0, (
        f"six of twenty-four hours hold no sample at all and the page reported "
        f"{result['uptime']}%: absence of a ping is not evidence of uptime"
    )
    assert result["coverage"] == 75.0, result


def test_a_fully_observed_window_still_reads_100(db):
    """The control. Without it the refusal above would also hold on a
    computation that had stopped reporting a healthy system at all."""
    _seed(db, {i: True for i in range(TOTAL_SLOTS)})

    result = _window(db, COMP, WINDOW_H)

    assert result["uptime"] == 100.0, result
    assert result["coverage"] == 100.0, result


def test_a_not_ok_sample_still_lowers_it(db):
    """The behaviour that already worked, kept -- and told apart from the hole.

    Same uptime as the outage above, different coverage: one window was observed
    throughout and found broken, the other was not observed.
    """
    _seed(db, {i: i >= 72 for i in range(TOTAL_SLOTS)})

    result = _window(db, COMP, WINDOW_H)

    assert result["uptime"] == 75.0, result
    assert result["coverage"] == 100.0, result


def test_many_samples_in_one_slot_count_once(db):
    """The status page writes a ping on every request and refreshes every 30s.

    One open tab therefore contributes 120 samples an hour against the
    scheduler's 12, and every one of them is taken while the API is answering.
    Under the old computation an hour of that traffic and twenty-three hours of
    silence read as 100%.
    """
    since = datetime.utcnow() - timedelta(hours=WINDOW_H)
    for minute in range(60):                       # the last hour of the window
        for half in (0, 30):
            db.add(HealthPing(
                component=COMP, ok=True,
                # Ten seconds into the slot: minute 0 second 0 is exactly a
                # boundary, and the reader's `since` is milliseconds later
                # than this one, which would move that sample a slot back.
                ts=since + timedelta(hours=WINDOW_H - 1, minutes=minute,
                                     seconds=half + 10),
            ))
    db.commit()

    result = _window(db, COMP, WINDOW_H)

    observed_slots = 60 // SLOT
    assert result["uptime"] == round(observed_slots / TOTAL_SLOTS * 100, 2), result
    assert result["uptime"] < 5, (
        f"120 samples in one hour and silence for twenty-three reported "
        f"{result['uptime']}% uptime"
    )


def test_a_sparsely_sampled_component_leaves_the_upper_end_open(db):
    """A lower bound alone has the opposite failure to the one fixed here.

    `api` has no sampler on a cadence: the scheduler container cannot observe
    the API and stopped claiming it (tests/test_the_scheduler_does_not_vouch_for
    _the_api.py), so those rows arrive only when somebody opens the status page.
    Two page views in a day must not read as an outage -- the pair has to leave
    the upper end at 100, which is what the page renders as a range.
    """
    _seed(db, {10: True, 200: True})

    result = _window(db, COMP, WINDOW_H)

    assert result["uptime"] == result["coverage"], result
    assert result["uptime"] < 1, result

    upper = result["uptime"] + (100 - result["coverage"])
    assert upper == 100.0, (
        f"two samples in a day left an upper end of {upper}%, so a healthy "
        f"component with no scheduled sampler reads as an outage"
    )


def test_an_empty_window_answers_nothing_rather_than_zero(db):
    """No samples at all is "—" on the page, as before: a component that has
    never been recorded is not a component that was down."""
    assert _window(db, COMP, WINDOW_H)["uptime"] is None
    assert _uptime(db, COMP, WINDOW_H) is None


def test_the_recorder_and_the_scheduler_agree_on_the_cadence():
    """The denominator is only right if the ping really arrives that often.

    The literal stays in `app/scheduler.py` so `CLAUDE.md`'s job table keeps
    telling an operator the period -- naming the constant there would leave the
    table saying `IntervalTrigger(minutes=HEALTH_PING_INTERVAL_MINUTES)`. The
    duplication is pinned here instead of hidden.
    """
    from tests.test_scheduler_jobs_match_the_doc import jobs_in_code

    trigger = jobs_in_code()["health_ping"]
    found = re.search(r"minutes=(\d+)", trigger)

    assert found, f"health_ping is no longer a minute interval: {trigger}"
    assert int(found.group(1)) == HEALTH_PING_INTERVAL_MINUTES, (
        f"the scheduler pings every {found.group(1)} minutes and the uptime "
        f"denominator assumes every {HEALTH_PING_INTERVAL_MINUTES}"
    )


def test_the_payload_carries_the_coverage_and_the_interval(monkeypatch):
    """What the page needs to render the figure honestly.

    `_component_ok` is replaced so this exercises the payload rather than the
    model, database and external-data checks it would otherwise run.
    """
    import app.services.status_service as svc

    monkeypatch.setattr(svc, "_component_ok", lambda: {
        "api": True, "models": True, "database": True, "external_data": True})

    body = status_summary()

    assert body["sample_interval_minutes"] == HEALTH_PING_INTERVAL_MINUTES
    assert body["components"], "no components in the payload"
    for comp in body["components"]:
        for key in ("uptime_24h", "coverage_24h", "uptime_7d", "coverage_7d"):
            assert key in comp, f"{comp['component']} has no {key}: {comp}"
        assert comp["coverage_24h"] is not None, comp
