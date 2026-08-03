"""What a run actually achieved, rather than whether it threw.

`ingester_runs` recorded 'ok' whenever no exception escaped. Measured on
production on 2026-08-03:

    rosstat            69 runs   29325 rows    0 with no rows   ok
    sber_veb_baseline  69 runs    5865 rows    0 with no rows   ok
    openaq             62 runs       0 rows   62 with no rows   ok

Sixty-two runs reported success while writing nothing. The source had been
reachable, authenticated and healthy the whole time and simply had nothing to
give (issue #57: its stations stopped reporting in 2017). Nothing distinguished
that from the runs that worked, so a dashboard of green ticks was accurate about
execution and silent about data.

The point of splitting the verdict is that these are different questions with
different answers, and collapsing them loses the one that matters:

  execution_status       did the code finish, or did it raise
  primary_source_status  what the source gave: data, nothing, or an error
  data_outcome           what reached the database, from where
  status                 the single word, derived from the above, never set by hand
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional


# execution_status
COMPLETED = "completed"
FAILED = "failed"

# primary_source_status
DATA = "data"                # the source returned records
EMPTY = "empty"              # the source answered, and had nothing
UNAVAILABLE = "unavailable"  # the source could not be reached or refused
REJECTED = "rejected"        # records arrived and none survived validation

# data_outcome
PRIMARY = "primary"          # everything stored came from the expected source
PARTIAL = "partial"          # some records stored, some rejected
FALLBACK = "fallback"        # stored, but from a substitute source
NONE = "none"                # nothing stored

# status -- the summary, and the only field a dashboard should colour
SUCCESS = "success"
DEGRADED = "degraded"
FAILURE = "failed"


@dataclass(frozen=True)
class RunVerdict:
    execution_status: str
    primary_source_status: str
    data_outcome: str
    status: str
    records_received: int
    records_accepted: int
    records_rejected: int
    failure_reason: Optional[str] = None

    def as_dict(self) -> dict:
        return asdict(self)


def classify_run(
    *,
    raised: Optional[BaseException] = None,
    received: int = 0,
    accepted: int = 0,
    rejected: int = 0,
    used_fallback: bool = False,
    unreachable: bool = False,
) -> RunVerdict:
    """The verdict for one ingester run.

    Pure, so it can be enumerated over its whole input space rather than
    sampled. Every branch below corresponds to a state seen in production or to
    one the schema permits; there is no default that quietly means success.
    """
    if raised is not None:
        return RunVerdict(
            execution_status=FAILED,
            primary_source_status=UNAVAILABLE if unreachable else DATA,
            data_outcome=PARTIAL if accepted else NONE,
            status=FAILURE,
            records_received=received,
            records_accepted=accepted,
            records_rejected=rejected,
            failure_reason=f"{type(raised).__name__}: {raised}"[:500],
        )

    # Finished cleanly. What it achieved is a separate question, and this is the
    # one the old code never asked.
    if unreachable:
        # Reached nothing, but handled it without raising -- which is exactly how
        # a silent success is manufactured.
        return RunVerdict(
            execution_status=COMPLETED,
            primary_source_status=UNAVAILABLE,
            data_outcome=FALLBACK if accepted else NONE,
            status=DEGRADED,
            records_received=received,
            records_accepted=accepted,
            records_rejected=rejected,
            failure_reason="the primary source could not be reached",
        )

    if received == 0:
        # The openaq case: healthy, authenticated, and empty. Not a failure of
        # this run, and not a success either -- freshness for that source has to
        # stop advancing.
        return RunVerdict(
            execution_status=COMPLETED,
            primary_source_status=EMPTY,
            data_outcome=FALLBACK if accepted else NONE,
            status=DEGRADED,
            records_received=0,
            records_accepted=accepted,
            records_rejected=rejected,
            failure_reason="the source returned no records",
        )

    if accepted == 0:
        # Records arrived and none survived validation. Worse than empty: it
        # means the source is producing something we cannot use, which a
        # zero-row count alone would not distinguish from silence.
        return RunVerdict(
            execution_status=COMPLETED,
            primary_source_status=REJECTED,
            data_outcome=NONE,
            status=DEGRADED,
            records_received=received,
            records_accepted=0,
            records_rejected=rejected,
            failure_reason=f"all {received} records were rejected by validation",
        )

    outcome = FALLBACK if used_fallback else (PARTIAL if rejected else PRIMARY)
    return RunVerdict(
        execution_status=COMPLETED,
        primary_source_status=DATA,
        data_outcome=outcome,
        # Only here. A run counts as a success when the expected source produced
        # records and all of them reached the database -- a measurable result,
        # not merely an absence of exceptions. A partial write or a fallback is
        # degraded: something was stored, but not what was asked for.
        status=SUCCESS if outcome == PRIMARY else DEGRADED,
        records_received=received,
        records_accepted=accepted,
        records_rejected=rejected,
        # Every verdict short of success has to say why, including this one.
        # A fallback run with nothing rejected was landing on `degraded` with no
        # reason at all -- found by enumerating the input space, not by any of
        # the individual cases below it, which each asserted only what they
        # happened to care about.
        failure_reason=_reason(outcome, received, rejected),
    )


def _reason(outcome: str, received: int, rejected: int) -> Optional[str]:
    if outcome == PRIMARY:
        return None
    if outcome == FALLBACK:
        return "records came from a fallback source, not the primary one"
    return f"{rejected} of {received} records were rejected"
