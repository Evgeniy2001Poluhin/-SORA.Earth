"""What the operator does next, enumerated rather than sampled.

#74: `degraded` is a display. Reading it, nobody can tell whether to wait,
retry, page someone, or ignore it, and that is the decision the run record
exists to support. `required_action` is derived from the verdict and the age of
the data, so it cannot drift away from either.

The pair that carries the weight:

    empty source, newest signal 20 minutes old    wait
    empty source, newest signal nine years old    escalate

Both are `degraded`. The second is #57 -- 333 consecutive runs that looked
exactly like the first.
"""
import itertools

import pytest

from app.ingesters.classification import (
    ACTION_ESCALATE, ACTION_INVESTIGATE, ACTION_NONE, ACTION_WAIT,
    ACTION_SEVERITY,
    REASON_CODES, REASON_CRASHED, REASON_FALLBACK_USED, REASON_OK,
    REASON_PARTIAL_WRITE, REASON_SOURCE_EMPTY, REASON_SOURCE_REJECTED,
    REASON_SOURCE_UNAVAILABLE, REASON_WRITE_FAILED,
    classify_run, required_action,
)

HOUR = 3600.0
NINE_YEARS = 9 * 365 * 24 * HOUR

# Every input the runner can hand it: a verdict from each branch of the
# classifier, crossed with every shape of freshness knowledge.
VERDICTS = {
    REASON_OK: dict(received=10, accepted=10, rejected=0),
    REASON_SOURCE_EMPTY: dict(received=0, accepted=0, rejected=0),
    REASON_SOURCE_UNAVAILABLE: dict(unreachable=True),
    REASON_SOURCE_REJECTED: dict(received=10, accepted=0, rejected=10),
    REASON_WRITE_FAILED: dict(received=10, accepted=0, rejected=0, write_failed=True),
    REASON_PARTIAL_WRITE: dict(received=10, accepted=7, rejected=3),
    REASON_FALLBACK_USED: dict(received=10, accepted=10, rejected=0, used_fallback=True),
    REASON_CRASHED: dict(raised=RuntimeError("boom"), received=0),
}

FRESHNESS = [
    (None, None),           # nothing measured
    (None, 24 * HOUR),      # tolerance known, age not
    (HOUR, None),           # age known, tolerance not
    (HOUR, 24 * HOUR),      # fresh
    (NINE_YEARS, 24 * HOUR),  # #57
    (24 * HOUR, 24 * HOUR),   # exactly at the boundary
]


def _verdict(reason):
    v = classify_run(**VERDICTS[reason])
    assert v.reason_code == reason, (
        f"the inputs for {reason} now classify as {v.reason_code}; this table "
        f"no longer covers the branch it names"
    )
    return v


def test_the_table_covers_every_code():
    """Otherwise the enumeration below tests the codes I happened to list."""
    assert set(VERDICTS) == set(REASON_CODES), {
        "missing from the table": sorted(REASON_CODES - set(VERDICTS)),
        "not a real code": sorted(set(VERDICTS) - REASON_CODES),
    }


@pytest.mark.parametrize("reason", sorted(VERDICTS))
@pytest.mark.parametrize("age,tolerance", FRESHNESS)
def test_every_combination_yields_a_known_action(reason, age, tolerance):
    action = required_action(_verdict(reason), age_seconds=age,
                             tolerance_seconds=tolerance)

    assert action in ACTION_SEVERITY, action

    # `none` is reserved for a clean run. The whole defect being closed is a
    # degraded state that reads as "nothing to do".
    if action == ACTION_NONE:
        assert reason == REASON_OK, (
            f"{reason} with age={age} tolerance={tolerance} says there is "
            f"nothing to do"
        )


@pytest.mark.parametrize("reason", sorted(set(VERDICTS) - {REASON_OK}))
def test_wait_is_never_asserted_without_measuring(reason):
    """`wait` means "fine for now", which unmeasured age cannot support.

    This is the original defect one level up: a state that looks settled
    because nobody looked.
    """
    for age, tolerance in [(None, None), (None, 24 * HOUR), (HOUR, None)]:
        action = required_action(_verdict(reason), age_seconds=age,
                                 tolerance_seconds=tolerance)
        assert action != ACTION_WAIT, (
            f"{reason} with age={age} tolerance={tolerance} claims things are "
            f"fine without having measured whether they are"
        )


def test_the_openaq_pair():
    """The two cases the issue is about, side by side."""
    empty = _verdict(REASON_SOURCE_EMPTY)

    assert required_action(empty, age_seconds=20 * 60,
                           tolerance_seconds=HOUR) == ACTION_WAIT
    assert required_action(empty, age_seconds=NINE_YEARS,
                           tolerance_seconds=HOUR) == ACTION_ESCALATE

    # Both are the same word today.
    assert empty.status == "degraded"


def test_the_boundary_is_not_stale():
    """Age exactly equal to the tolerance is still within it.

    Stated because `>` and `>=` are equally plausible here, and a source polled
    on exactly its own cadence would otherwise escalate every single run.
    """
    empty = _verdict(REASON_SOURCE_EMPTY)

    assert required_action(empty, age_seconds=24 * HOUR,
                           tolerance_seconds=24 * HOUR) == ACTION_WAIT
    assert required_action(empty, age_seconds=24 * HOUR + 1,
                           tolerance_seconds=24 * HOUR) == ACTION_ESCALATE


def test_staleness_outranks_the_immediate_reason():
    """A run that half-worked while the data aged out is still unusable."""
    for reason in (REASON_PARTIAL_WRITE, REASON_FALLBACK_USED,
                   REASON_SOURCE_REJECTED, REASON_CRASHED, REASON_WRITE_FAILED):
        assert required_action(_verdict(reason), age_seconds=NINE_YEARS,
                               tolerance_seconds=HOUR) == ACTION_ESCALATE, reason


def test_our_own_failures_are_investigated_not_waited_on():
    """A crash or a failed write is not something that resolves by waiting."""
    for reason in (REASON_CRASHED, REASON_WRITE_FAILED, REASON_SOURCE_REJECTED,
                   REASON_PARTIAL_WRITE, REASON_FALLBACK_USED):
        assert required_action(_verdict(reason), age_seconds=HOUR,
                               tolerance_seconds=24 * HOUR) == ACTION_INVESTIGATE, reason


def test_a_clean_run_asks_for_nothing_whatever_the_age():
    """Freshness is not this run's business when this run worked.

    A source that has just written is fresh by construction; an old age
    alongside a successful primary write means the age was measured over
    something else, and inventing an escalation from it would be noise.
    """
    ok = _verdict(REASON_OK)

    for age, tolerance in FRESHNESS:
        assert required_action(ok, age_seconds=age,
                               tolerance_seconds=tolerance) == ACTION_NONE


def test_severity_orders_the_actions():
    """An operator view sorts by this rather than by time."""
    assert ACTION_SEVERITY[ACTION_ESCALATE] > ACTION_SEVERITY[ACTION_INVESTIGATE]
    assert ACTION_SEVERITY[ACTION_INVESTIGATE] > ACTION_SEVERITY[ACTION_WAIT]
    assert ACTION_SEVERITY[ACTION_WAIT] > ACTION_SEVERITY[ACTION_NONE]
    assert set(ACTION_SEVERITY) == {ACTION_NONE, ACTION_WAIT, ACTION_INVESTIGATE,
                                    ACTION_ESCALATE}


# --- the codes themselves ---------------------------------------------------


@pytest.mark.parametrize("reason", sorted(VERDICTS))
def test_every_branch_carries_a_code_from_the_vocabulary(reason):
    v = _verdict(reason)

    assert v.reason_code in REASON_CODES
    # Prose and code must agree about whether anything went wrong.
    assert (v.failure_reason is None) == (v.reason_code == REASON_OK), v


def test_the_prose_is_not_the_grouping_key():
    """Two runs of the same kind share a code even when the sentence differs.

    `failure_reason` interpolates counts, so grouping by it produces one bucket
    per distinct number -- which is why "is this the same thing as yesterday"
    could not be answered without a person reading them.
    """
    a = classify_run(received=10, accepted=0, rejected=10)
    b = classify_run(received=4000, accepted=0, rejected=4000)

    assert a.failure_reason != b.failure_reason
    assert a.reason_code == b.reason_code == REASON_SOURCE_REJECTED


def test_classification_and_action_agree_across_the_whole_input_space():
    """No combination of raw inputs produces a clean run that needs attention.

    Walked over the classifier's inputs rather than over my table of verdicts,
    so a new branch added there is covered here without anyone remembering to
    add it.
    """
    counts = [(0, 0, 0), (10, 10, 0), (10, 7, 3), (10, 0, 10)]
    flags = itertools.product([None, RuntimeError("boom")],
                              [False, True], [False, True], [False, True])

    for (received, accepted, rejected), (raised, unreachable, fallback, wfail) in \
            itertools.product(counts, flags):
        v = classify_run(raised=raised, received=received, accepted=accepted,
                         rejected=rejected, unreachable=unreachable,
                         used_fallback=fallback, write_failed=wfail)

        assert v.reason_code in REASON_CODES, v
        action = required_action(v, age_seconds=HOUR, tolerance_seconds=24 * HOUR)

        assert (action == ACTION_NONE) == (v.status == "success"), (
            f"action {action} and status {v.status} disagree about whether "
            f"this run needs anything: {v}"
        )
