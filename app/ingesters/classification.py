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
UNKNOWN = "unknown"          # the run died before the source could answer

# data_outcome
PRIMARY = "primary"          # everything stored came from the expected source
PARTIAL = "partial"          # some records stored, some rejected
FALLBACK = "fallback"        # stored, but from a substitute source
NONE = "none"                # nothing stored

# status -- the summary, and the only field a dashboard should colour
SUCCESS = "success"
DEGRADED = "degraded"
FAILURE = "failed"

# reason_code -- a closed vocabulary (#74).
#
# `failure_reason` is prose written for a person to read. Prose cannot be
# grouped, counted, alerted on, or compared between two runs, so every question
# an operator has -- "is this the same thing as yesterday", "how often" --
# needed someone to read the sentence and decide. These are the same facts as a
# value.
REASON_OK = "ok"
REASON_SOURCE_EMPTY = "source_empty"
REASON_SOURCE_UNAVAILABLE = "source_unavailable"
REASON_SOURCE_REJECTED = "source_rejected"
REASON_WRITE_FAILED = "write_failed"
REASON_PARTIAL_WRITE = "partial_write"
REASON_FALLBACK_USED = "fallback_used"
REASON_CRASHED = "crashed"

REASON_CODES = frozenset({
    REASON_OK, REASON_SOURCE_EMPTY, REASON_SOURCE_UNAVAILABLE,
    REASON_SOURCE_REJECTED, REASON_WRITE_FAILED, REASON_PARTIAL_WRITE,
    REASON_FALLBACK_USED, REASON_CRASHED,
})

# required_action -- what the operator does next (#74).
#
# The field the issue is actually about. `degraded` is a display: reading it,
# nobody can tell whether to wait, retry, page someone, or ignore it. This is
# the only field that answers that, and it is derived rather than set.
ACTION_NONE = "none"
ACTION_WAIT = "wait"
ACTION_INVESTIGATE = "investigate"
ACTION_ESCALATE = "escalate"

# Ordered by how much attention each demands. An operator view sorts by this,
# not by time -- an escalate from yesterday outranks a wait from a minute ago.
ACTION_SEVERITY = {
    ACTION_NONE: 0,
    ACTION_WAIT: 1,
    ACTION_INVESTIGATE: 2,
    ACTION_ESCALATE: 3,
}


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
    reason_code: str = REASON_OK

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
    write_failed: bool = False,
) -> RunVerdict:
    """The verdict for one ingester run.

    Pure, so it can be enumerated over its whole input space rather than
    sampled. Every branch below corresponds to a state seen in production or to
    one the schema permits; there is no default that quietly means success.
    """
    if raised is not None:
        # Derived from what was actually seen, not assumed.
        #
        # This said DATA whenever `unreachable` was false -- so a crash before
        # the first request recorded "the source returned records" beside
        # records_received=0, which is a statement contradicted by the row it
        # sits in. A run that died before the source could answer knows nothing
        # about the source, and UNKNOWN says exactly that.
        if unreachable:
            source_status = UNAVAILABLE
        elif received > 0:
            source_status = DATA
        else:
            source_status = UNKNOWN
        return RunVerdict(
            execution_status=FAILED,
            primary_source_status=source_status,
            data_outcome=PARTIAL if accepted else NONE,
            status=FAILURE,
            records_received=received,
            records_accepted=accepted,
            records_rejected=rejected,
            failure_reason=f"{type(raised).__name__}: {raised}"[:500],
            reason_code=REASON_CRASHED,
        )

    # Finished cleanly. What it achieved is a separate question, and this is the
    # one the old code never asked.
    if write_failed:
        # The source did its part and the database did not.
        #
        # Without this, a persistence failure arrives as accepted=0 with a
        # rejected count and is read as REJECTED -- "the source is producing
        # something we cannot use" -- which points the investigation at the
        # wrong end of the pipe entirely.
        return RunVerdict(
            execution_status=COMPLETED,
            primary_source_status=DATA if received else UNKNOWN,
            data_outcome=NONE,
            status=FAILURE,
            records_received=received,
            records_accepted=accepted,
            records_rejected=rejected,
            failure_reason="records were fetched but could not be persisted",
            reason_code=REASON_WRITE_FAILED,
        )

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
            reason_code=REASON_SOURCE_UNAVAILABLE,
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
            reason_code=REASON_SOURCE_EMPTY,
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
            reason_code=REASON_SOURCE_REJECTED,
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
        reason_code=_reason_code(outcome),
    )


def _reason(outcome: str, received: int, rejected: int) -> Optional[str]:
    if outcome == PRIMARY:
        return None
    if outcome == FALLBACK:
        return "records came from a fallback source, not the primary one"
    return f"{rejected} of {received} records were rejected"


def _reason_code(outcome: str) -> str:
    if outcome == PRIMARY:
        return REASON_OK
    if outcome == FALLBACK:
        return REASON_FALLBACK_USED
    return REASON_PARTIAL_WRITE


# Codes where the run itself is fine and the question is entirely about how old
# the data has become. Everything else is a problem with this run.
_STALENESS_DECIDES = frozenset({REASON_SOURCE_EMPTY, REASON_SOURCE_UNAVAILABLE})


def required_action(
    verdict: RunVerdict,
    *,
    age_seconds: Optional[float] = None,
    tolerance_seconds: Optional[float] = None,
) -> str:
    """What an operator does next. Derived, never set.

    #74: `degraded` is a display. Reading it, nobody can tell whether to wait,
    retry, page someone, or ignore it -- and that is the decision the record
    exists to support.

    The distinction that carries the weight is between an empty source whose
    data is still fresh and an empty source whose data is not:

        openaq, empty, newest signal 20 minutes old      wait
        openaq, empty, newest signal nine years old      escalate

    Both are `degraded`. The second is #57, which ran for 333 consecutive runs
    looking exactly like the first.

    `tolerance_seconds` comes from the ingester's own `default_ttl_hours`, which
    every source already declares -- 1 hour for openmeteo, 180 days for rosstat.
    A source is not stale because a wall-clock threshold says so; it is stale
    against the cadence it claims for itself.

    When freshness is unknown the answer is `investigate`, never `wait`. `wait`
    is an assertion that things are fine for now, and an unmeasured age does not
    support it -- that is the shape of the original defect, one level up.
    """
    if verdict.reason_code == REASON_OK:
        return ACTION_NONE

    stale = (
        age_seconds is not None
        and tolerance_seconds is not None
        and age_seconds > tolerance_seconds
    )
    if stale:
        # Regardless of the immediate reason. A run that partially wrote while
        # the data as a whole aged past its tolerance is still a source nobody
        # can rely on.
        return ACTION_ESCALATE

    if verdict.reason_code in _STALENESS_DECIDES:
        if age_seconds is None or tolerance_seconds is None:
            return ACTION_INVESTIGATE
        return ACTION_WAIT

    # crashed, write_failed, source_rejected, partial_write, fallback_used:
    # something is wrong on a side somebody here controls.
    return ACTION_INVESTIGATE
