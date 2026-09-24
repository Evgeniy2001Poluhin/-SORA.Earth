"""§3 of the M3 declaration: how a day is formed, in code.

`docs/M3_FORECAST_DECLARATION.md` fixed the rule before any accumulation
counted:

    target(point, day) = mean of the hourly values of that UTC day, if at least
                         19 of the 24 hours are present; otherwise the day is
                         absent

and Amendment 1.3 settled the ambiguity the data exposed: "observations" there
means **distinct UTC hours**, not rows. The ingester reads Open-Meteo's
`current` block, whose `time` moves at 15-minute resolution, so an hour can hold
several rows -- and every deployment adds one, because `auto_openmeteo_ingestion`
is in `RUN_IMMEDIATELY_ON_STARTUP`. Measured on production 2026-09-19: 3620
point-hours held more than one row, the two readings of the daily value differed
by up to 0.665 °C, and by exactly 0.000 on days with one sample per hour. A
target that moves with how often the scheduler restarted is the defect §3's own
rationale rejects when it refuses the last-observation rule.

Until this module existed the rule was prose. Nothing built the series it
describes, which meant it could not be implemented wrongly -- and could not be
implemented at all.

The output is `entry_conditions.Observation`, so the §7 gate reads this directly
rather than through a second shape that could disagree with it.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Dict, Iterable, List, Tuple

from .entry_conditions import MIN_COVERAGE, Observation

#: Hours an hourly source is expected to supply in a UTC day. The same figure
#: `app/api/infra.py` uses for the coverage endpoint; both read the floor off
#: MIN_COVERAGE rather than carrying a literal 19, which is what §3 asks for --
#: "one number rather than two that could drift apart".
EXPECTED_HOURS_PER_DAY = 24


def required_hours(expected: int = EXPECTED_HOURS_PER_DAY) -> int:
    """The floor, derived. `int()` and not `round()`: 24 x 0.80 is 19.2, and the
    endpoint that reports gaps truncates it the same way."""
    return int(expected * MIN_COVERAGE)


def daily_observations(
    rows: Iterable[Tuple[str, datetime, float]],
    expected_hours: int = EXPECTED_HOURS_PER_DAY,
) -> List[Observation]:
    """Build the declared daily target from raw hourly-ish samples.

    `rows` are `(point, event_time, value)`. `event_time` is converted to UTC;
    a naive timestamp is assumed to be UTC already, which is what the ingester
    writes and what `environmental_observations.event_time` stores.

    Two means, not one. An hour's value is the mean of its samples, and the
    day's value is the mean of its hours -- so an hour sampled twice contributes
    once, and the result does not move when the polling schedule does.

    A day below the floor is **omitted**, never interpolated: §7.1 lists an
    interpolated point among the things that do not count as movement, and a gap
    has to reach the gate as a gap.
    """
    floor = required_hours(expected_hours)

    buckets: Dict[Tuple[str, date, int], List[float]] = defaultdict(list)
    for point, event_time, value in rows:
        if value is None or event_time is None:
            continue
        moment = _as_utc(event_time)
        buckets[(point, moment.date(), moment.hour)].append(float(value))

    hourly: Dict[Tuple[str, date], Dict[int, float]] = defaultdict(dict)
    for (point, day, hour), values in buckets.items():
        hourly[(point, day)][hour] = sum(values) / len(values)

    out: List[Observation] = []
    for (point, day), hours in hourly.items():
        if len(hours) < floor:
            continue
        out.append(Observation(
            region=point,
            # A daily target's period is the day: it starts and ends on the same
            # date. `period_end` is the axis §7 orders and windows on.
            period_start=day,
            period_end=day,
            value=sum(hours.values()) / len(hours),
        ))

    return sorted(out, key=lambda o: (o.region, o.period_end))


def snapshot_digest(
    rows: Iterable[Tuple[str, datetime, float]],
) -> str:
    """`sha256:` over the observations a report was computed from.

    Phase 7's exit criterion is that every result ties to a source and a
    snapshot. The report's counts -- points, days, observations -- do not
    identify anything: two runs over different data can produce the same three
    numbers. This is the same closure `_do_retrain` makes for the training file
    (#354), on the side that reads observations instead of a CSV.

    **Sorted before hashing, deliberately.** The loader orders by `event_time`
    today and nothing forces that to stay true; an index change or a UNION would
    reorder it, and a digest that moved then would be reporting a data change
    that did not happen. Sorting makes the fingerprint a property of the set of
    observations rather than of the query that fetched them.

    Values are formatted with `repr` rather than rounded: rounding would make
    two genuinely different readings hash alike, which is the failure a
    fingerprint exists to prevent.

    Timestamps go through the same UTC normalisation the series uses, so a row
    fetched as naive and the same row fetched as aware do not look like two
    different observations.
    """
    canonical = sorted(
        (point, _as_utc(event_time).isoformat(), repr(float(value)))
        for point, event_time, value in rows
        if value is not None and event_time is not None
    )
    joined = "\n".join("\t".join(row) for row in canonical)
    return "sha256:" + hashlib.sha256(joined.encode("utf-8")).hexdigest()


def coverage_by_day(
    rows: Iterable[Tuple[str, datetime, float]],
) -> Dict[Tuple[str, date], int]:
    """Distinct UTC hours covered, per point per day.

    Reported beside a series rather than derived from it, because the series
    cannot say why a day is missing -- and "no day fell short" and "no day was
    examined" are different facts about a deployment.
    """
    seen: Dict[Tuple[str, date], set] = defaultdict(set)
    for point, event_time, value in rows:
        if value is None or event_time is None:
            continue
        moment = _as_utc(event_time)
        seen[(point, moment.date())].add(moment.hour)
    return {key: len(hours) for key, hours in seen.items()}


def _as_utc(moment: datetime) -> datetime:
    """Naive timestamps are UTC. Anything else is converted rather than assumed.

    The declaration says UTC calendar day; taking a host-local boundary would
    move every day's membership by the timezone of whatever ran the query, and
    nothing downstream could detect it.
    """
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(timezone.utc).replace(tzinfo=None)
