"""The §7 entry gate, executable, with the variation condition of v1.1.

v1.0 fixed six conditions before any data existed: a training window, exactly
twelve non-overlapping test windows, coverage per region per window, all
declared regions carrying the full set of folds, one weight per region-day, and
a pinned region set. Every one of them counts records.

§10.2 recorded what that leaves open, in the protocol's own words:

    The §7 entry conditions would be satisfied on schedule by a series
    containing nothing. Twelve non-overlapping windows at >=80% coverage is a
    test of record count, and repeated writes of a constant pass it. This is the
    one way the gate as written can be met without the thing it was gating for.

Measured at the time: 1.00 distinct values per region over eight days, for all
five rosstat metrics and the sber baseline.

v1.1 adds movement, and this module is it -- executable rather than prose,
amended under §9 before any run exists, so no computed result is rewritten:
none has been computed.

**Movement is between source periods, not between rows.** Four things that look
like new information and are not:

    a repeated write of the same period and value   the upsert of #121, whose
                                                    whole point is that an
                                                    unchanged snapshot produces
                                                    no new observation
    a different ingested_at                         when the row was written,
                                                    which is the conflation #121
                                                    removed
    a new source_revision with the same value       the content was restated,
                                                    not changed
    an interpolated point                           #132: resampling fills every
                                                    calendar day, so "a date is
                                                    present" stops meaning
                                                    "something was observed"

The rule, as narrowly as it can be put:

    In every test window, for every declared region, the target must take a
    different value in at least two distinct source periods.

Not a threshold on magnitude, which would be a tuned parameter smuggled into a
pre-registration. A window in which the target does not move gives every method
the same exact answer -- the last-value baseline of §4 scores MAE = 0 there --
so averaging such windows in dilutes the metric toward zero instead of
measuring anything.

The consequence is a real constraint rather than a technicality, and it is the
finding of §10.3 in a form the gate can check: **a target cannot be evaluated at
a horizon shorter than its own cadence.** A quantity published quarterly does
not move inside a 7-day window, and no amount of accumulated history changes
that.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# §7 v1.0, unchanged by this amendment.
REQUIRED_WINDOWS = 12
MIN_COVERAGE = 0.80
TRAINING_DAYS = {7: 90, 30: 180}


@dataclass(frozen=True)
class Observation:
    """One target value, as the source published it.

    `period_end` is the axis -- the end of the period the value describes, never
    when the row was written. `interpolated` marks a point produced by filling,
    which is not an observation (#132).
    """

    region: str
    period_start: date
    period_end: date
    value: float
    revision: Optional[str] = None
    interpolated: bool = False


@dataclass(frozen=True)
class ConditionResult:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class GateVerdict:
    passed: bool
    horizon: int
    conditions: List[ConditionResult] = field(default_factory=list)

    def failures(self) -> List[ConditionResult]:
        return [c for c in self.conditions if not c.passed]

    def as_text(self) -> str:
        lines = [f"section 7 entry gate, h={self.horizon}: "
                 f"{'PASSED' if self.passed else 'REFUSED'}"]
        for c in self.conditions:
            lines.append(f"  {'ok  ' if c.passed else 'FAIL'} {c.name}: {c.detail}")
        return "\n".join(lines)


def real_observations(observations: Iterable[Observation]) -> List[Observation]:
    """What is left once the things that only look like observations are gone.

    Interpolated points are dropped outright. Repeats of a period the source has
    already published collapse to one, keeping the first: a restatement carrying
    a new revision but the same value is the same observation said twice, and a
    restatement that *changes* the value is a correction, which is a different
    matter the protocol handles under vintage (§2) rather than here.
    """
    seen: Dict[Tuple[str, date, date], Observation] = {}
    for obs in observations:
        if obs.interpolated:
            continue
        key = (obs.region, obs.period_start, obs.period_end)
        seen.setdefault(key, obs)
    return sorted(seen.values(), key=lambda o: (o.region, o.period_end))


def movements(observations: Sequence[Observation]) -> int:
    """How many times the value changes between consecutive distinct periods."""
    ordered = sorted(observations, key=lambda o: o.period_end)
    return sum(
        1 for a, b in zip(ordered, ordered[1:]) if a.value != b.value
    )


def _windows(last_day: date, horizon: int) -> List[Tuple[date, date]]:
    """Twelve non-overlapping windows of `horizon` days, ending at `last_day`.

    Built backwards from the end so the arithmetic is one subtraction and cannot
    drift; §5.1 fixes the construction and this follows it.
    """
    windows = []
    end = last_day
    for _ in range(REQUIRED_WINDOWS):
        start = end - timedelta(days=horizon - 1)
        windows.append((start, end))
        end = start - timedelta(days=1)
    return list(reversed(windows))


def evaluate(observations: Iterable[Observation], *,
             declared_regions: Iterable[str], horizon: int,
             last_day: date) -> GateVerdict:
    """Apply §7 v1.1 to a candidate target."""
    declared = sorted(declared_regions)
    windows = _windows(last_day, horizon)
    earliest = windows[0][0]
    conditions: List[ConditionResult] = []

    real = real_observations(observations)
    by_region: Dict[str, List[Observation]] = {}
    for obs in real:
        by_region.setdefault(obs.region, []).append(obs)

    # An empty candidate cannot pass. Stated first and separately: without it
    # every condition below is vacuously true over an empty set, which is the
    # oldest way a gate opens on nothing.
    conditions.append(ConditionResult(
        "the candidate has observations at all",
        bool(real),
        f"{len(real)} real observations after dropping interpolated and "
        f"repeated periods",
    ))

    required_training = TRAINING_DAYS[horizon]
    first = min((o.period_end for o in real), default=None)
    have = None if first is None else (earliest - first).days
    conditions.append(ConditionResult(
        "training window",
        have is not None and have >= required_training,
        f"{have} days before the earliest window, need >= {required_training}",
    ))

    conditions.append(ConditionResult(
        "twelve non-overlapping windows",
        len(windows) == REQUIRED_WINDOWS,
        f"{len(windows)} windows of {horizon} days",
    ))

    absent = [r for r in declared if r not in by_region]
    conditions.append(ConditionResult(
        "every declared region present",
        not absent,
        f"{len(absent)} of {len(declared)} declared regions absent"
        + (f": {absent[:5]}" if absent else ""),
    ))

    thin: List[str] = []
    for region in declared:
        points = by_region.get(region, [])
        for start, end in windows:
            covered = sum(1 for o in points if start <= o.period_end <= end)
            if covered / horizon < MIN_COVERAGE:
                thin.append(f"{region}@{start.isoformat()}")
    conditions.append(ConditionResult(
        f"coverage >= {MIN_COVERAGE:.0%} per region per window",
        not thin,
        f"{len(thin)} region-windows below the floor"
        + (f", first {thin[:3]}" if thin else ""),
    ))

    # --- v1.1 ---------------------------------------------------------------
    still: List[str] = []
    for region in declared:
        points = by_region.get(region, [])
        for start, end in windows:
            inside = [o for o in points if start <= o.period_end <= end]
            if movements(inside) < 1:
                still.append(f"{region}@{start.isoformat()}")
    conditions.append(ConditionResult(
        "the target moves between source periods, in every window",
        bool(real) and not still,
        f"{len(still)} region-windows in which the target never changed"
        + (f", first {still[:3]}" if still else ""),
    ))

    return GateVerdict(
        passed=all(c.passed for c in conditions),
        horizon=horizon,
        conditions=conditions,
    )
