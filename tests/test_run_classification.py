"""The classifier is enumerated, not sampled.

It is a pure function over a small input space, so there is no excuse for
checking a handful of cases and trusting the rest. The whole space is walked
below and every combination is asserted against the invariants; the cases seen
in production are then pinned individually.

The invariant that matters is the one the old code violated: `success` must mean
a measurable result from the expected source, never merely the absence of an
exception.
"""
import itertools

import pytest

from app.ingesters.classification import (
    classify_run,
    COMPLETED, FAILED,
    DATA, EMPTY, UNAVAILABLE, REJECTED, UNKNOWN,
    PRIMARY, PARTIAL, FALLBACK, NONE,
    SUCCESS, DEGRADED, FAILURE,
)

# received, accepted, rejected combinations that a persist result can actually
# produce: accepted + rejected never exceeds received, and nothing is negative.
COUNTS = [
    (0, 0, 0),      # the source gave nothing
    (10, 10, 0),    # everything stored
    (10, 7, 3),     # some stored, some rejected
    (10, 0, 10),    # everything rejected
]
FLAGS = list(itertools.product(
    [None, RuntimeError("boom")], [False, True], [False, True], [False, True]
))


@pytest.mark.parametrize("received,accepted,rejected", COUNTS)
@pytest.mark.parametrize("raised,unreachable,used_fallback,write_failed", FLAGS)
def test_every_combination_obeys_the_invariants(
    received, accepted, rejected, raised, unreachable, used_fallback, write_failed
):
    v = classify_run(
        raised=raised, received=received, accepted=accepted,
        rejected=rejected, unreachable=unreachable, used_fallback=used_fallback,
        write_failed=write_failed,
    )

    assert v.execution_status in (COMPLETED, FAILED)
    assert v.primary_source_status in (DATA, EMPTY, UNAVAILABLE, REJECTED, UNKNOWN)
    assert v.data_outcome in (PRIMARY, PARTIAL, FALLBACK, NONE)
    assert v.status in (SUCCESS, DEGRADED, FAILURE)

    # The invariant the old code broke: 62 production runs recorded success
    # having written nothing at all.
    if v.status == SUCCESS:
        assert v.execution_status == COMPLETED, v
        assert v.primary_source_status == DATA, v
        assert v.data_outcome == PRIMARY, v
        assert v.records_accepted > 0, v
        assert raised is None, v
        assert not unreachable, v
        assert not write_failed, v

    # A run that raised is never anything but failed.
    if raised is not None:
        assert v.execution_status == FAILED, v
        assert v.status == FAILURE, v
        assert v.failure_reason, v

    # A write failure is a failure, never an empty source and never degraded.
    # Read as either, a database outage sends the investigation to the API.
    if write_failed and raised is None:
        assert v.status == FAILURE, v
        assert v.primary_source_status != EMPTY, v

    # Nothing stored is never a success, whatever the reason.
    if v.records_accepted == 0:
        assert v.status != SUCCESS, v

    # Anything short of success must say why. A degraded run with no reason is
    # as unreadable as the "ok" it replaces.
    if v.status != SUCCESS:
        assert v.failure_reason, v

    assert v.records_received == received
    assert v.records_accepted == accepted
    assert v.records_rejected == rejected


def test_success_is_reachable():
    """A guard that can never pass is as useless as one that always does."""
    v = classify_run(received=425, accepted=425)
    assert v.status == SUCCESS
    assert v.failure_reason is None


# --- the cases that actually happened ---------------------------------------

def test_openaq_healthy_and_empty():
    """62 production runs: reachable, authenticated, and nothing to give.

    Recorded 'ok' every time. It is not a failure of the run and not a success;
    the distinction is the whole point of this change.
    """
    v = classify_run(received=0, accepted=0)
    assert v.execution_status == COMPLETED
    assert v.primary_source_status == EMPTY
    assert v.data_outcome == NONE
    assert v.status == DEGRADED
    assert "no records" in v.failure_reason


def test_rosstat_working():
    v = classify_run(received=425, accepted=425)
    assert (v.primary_source_status, v.data_outcome, v.status) == (DATA, PRIMARY, SUCCESS)


def test_records_arrived_and_none_survived_validation():
    """Distinct from empty: the source is producing something unusable.

    A zero-row count alone cannot tell the two apart, and they need different
    responses -- one is the source's silence, the other is our schema.
    """
    v = classify_run(received=10, accepted=0, rejected=10)
    assert v.primary_source_status == REJECTED
    assert v.status == DEGRADED
    assert "rejected by validation" in v.failure_reason


def test_partial_write_is_not_a_success():
    v = classify_run(received=10, accepted=7, rejected=3)
    assert v.data_outcome == PARTIAL
    assert v.status == DEGRADED
    assert "3 of 10" in v.failure_reason


def test_fallback_is_recorded_as_fallback():
    """#57's proposal: Open-Meteo where OpenAQ has nothing.

    Data was stored, so the run is not a failure -- but it did not come from the
    expected source, and calling that success is how the two get conflated in an
    analysis later.
    """
    v = classify_run(received=48, accepted=48, used_fallback=True)
    assert v.data_outcome == FALLBACK
    assert v.status == DEGRADED


def test_unreachable_without_raising_is_degraded_not_success():
    """The exact shape of a manufactured silent success: the error is handled,
    nothing is stored, and the old code would have written 'ok'."""
    v = classify_run(received=0, accepted=0, unreachable=True)
    assert v.primary_source_status == UNAVAILABLE
    assert v.status == DEGRADED
    assert "could not be reached" in v.failure_reason


def test_a_raise_carries_its_reason():
    v = classify_run(raised=ValueError("upstream 503"), unreachable=True)
    assert v.execution_status == FAILED
    assert v.primary_source_status == UNAVAILABLE
    assert "ValueError" in v.failure_reason
    assert "503" in v.failure_reason


# --- the cases review found --------------------------------------------------

def test_a_crash_before_the_source_answered_claims_nothing_about_it():
    """This reported DATA -- "the source returned records" -- beside
    records_received=0, a statement its own row contradicted."""
    v = classify_run(raised=RuntimeError("died in setup"))
    assert v.primary_source_status == UNKNOWN
    assert v.records_received == 0


def test_a_crash_after_records_arrived_does_say_the_source_answered():
    v = classify_run(raised=RuntimeError("died persisting"), received=12)
    assert v.primary_source_status == DATA


def test_a_write_failure_is_not_a_validation_rejection():
    """Both arrive as accepted=0 with a rejected count, and they point at
    opposite ends of the pipe. Read as REJECTED, a database outage sends the
    investigation to the source."""
    write = classify_run(received=10, accepted=0, rejected=10, write_failed=True)
    reject = classify_run(received=10, accepted=0, rejected=10)

    assert write.primary_source_status == DATA
    assert reject.primary_source_status == REJECTED
    assert write.status == FAILURE
    assert reject.status == DEGRADED
    assert "could not be persisted" in write.failure_reason


def test_a_write_failure_before_any_record_arrived_claims_nothing():
    v = classify_run(write_failed=True)
    assert v.primary_source_status == UNKNOWN
    assert v.status == FAILURE
