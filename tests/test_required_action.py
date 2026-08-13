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
    FRESH, FRESHNESS_NOT_APPLICABLE, FRESHNESS_STATES, FRESHNESS_UNKNOWN,
    NOT_CONFIGURED, STALE,
    REASON_CODES, REASON_CRASHED, REASON_FALLBACK_USED, REASON_OK,
    REASON_PARTIAL_WRITE, REASON_SOURCE_EMPTY, REASON_SOURCE_REJECTED,
    REASON_SOURCE_UNAVAILABLE, REASON_WRITE_FAILED,
    classify_run, freshness_status, required_action,
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


# --- the matrix, row by row -------------------------------------------------

MATRIX = [
    # (reason, freshness, action)
    (REASON_OK, FRESH, ACTION_NONE),
    (REASON_OK, STALE, ACTION_ESCALATE),
    (REASON_OK, FRESHNESS_NOT_APPLICABLE, ACTION_NONE),
    (REASON_OK, NOT_CONFIGURED, ACTION_NONE),
    (REASON_OK, FRESHNESS_UNKNOWN, ACTION_INVESTIGATE),

    (REASON_SOURCE_EMPTY, FRESH, ACTION_WAIT),
    (REASON_SOURCE_EMPTY, STALE, ACTION_ESCALATE),
    (REASON_SOURCE_EMPTY, FRESHNESS_NOT_APPLICABLE, ACTION_INVESTIGATE),
    (REASON_SOURCE_EMPTY, NOT_CONFIGURED, ACTION_INVESTIGATE),
    (REASON_SOURCE_EMPTY, FRESHNESS_UNKNOWN, ACTION_INVESTIGATE),

    (REASON_SOURCE_UNAVAILABLE, FRESH, ACTION_WAIT),
    (REASON_SOURCE_UNAVAILABLE, STALE, ACTION_ESCALATE),
    (REASON_SOURCE_UNAVAILABLE, NOT_CONFIGURED, ACTION_INVESTIGATE),

    (REASON_CRASHED, STALE, ACTION_ESCALATE),
    (REASON_CRASHED, FRESH, ACTION_INVESTIGATE),
    (REASON_WRITE_FAILED, STALE, ACTION_ESCALATE),
    (REASON_WRITE_FAILED, NOT_CONFIGURED, ACTION_INVESTIGATE),
    (REASON_SOURCE_REJECTED, FRESH, ACTION_INVESTIGATE),
    (REASON_PARTIAL_WRITE, FRESH, ACTION_INVESTIGATE),
    (REASON_FALLBACK_USED, FRESH, ACTION_INVESTIGATE),
]


@pytest.mark.parametrize("reason,freshness,expected", MATRIX)
def test_the_matrix_row(reason, freshness, expected):
    assert required_action(_verdict(reason), freshness=freshness) == expected


@pytest.mark.parametrize("reason", sorted(VERDICTS))
@pytest.mark.parametrize("freshness", sorted(FRESHNESS_STATES))
def test_every_combination_yields_a_known_action(reason, freshness):
    """The whole space, not the rows I wrote down."""
    action = required_action(_verdict(reason), freshness=freshness)

    assert action in ACTION_SEVERITY

    if action == ACTION_NONE:
        assert reason == REASON_OK, (
            f"{reason} with freshness={freshness} says there is nothing to do"
        )
    if action == ACTION_WAIT:
        assert freshness == FRESH, (
            f"`wait` on freshness={freshness}: it asserts things are fine, and "
            f"only a measured vintage inside a declared tolerance supports that"
        )


def test_stale_outranks_everything():
    """Including a clean run: the pipeline works, the data does not."""
    for reason in sorted(VERDICTS):
        assert required_action(_verdict(reason), freshness=STALE) == ACTION_ESCALATE


def test_an_unknown_freshness_state_is_refused():
    """A typo must not silently take the default branch."""
    with pytest.raises(ValueError):
        required_action(_verdict(REASON_OK), freshness="probably_fine")


def test_severity_orders_the_actions():
    """An operator view sorts by this rather than by time."""
    assert ACTION_SEVERITY[ACTION_ESCALATE] > ACTION_SEVERITY[ACTION_INVESTIGATE]
    assert ACTION_SEVERITY[ACTION_INVESTIGATE] > ACTION_SEVERITY[ACTION_WAIT]
    assert ACTION_SEVERITY[ACTION_WAIT] > ACTION_SEVERITY[ACTION_NONE]


# --- freshness_status itself ------------------------------------------------


def test_no_declared_tolerance_means_not_configured():
    """The defect this replaced: `default_ttl_hours` standing in for a vintage
    contract made every clean rosstat run escalate. Measured on production --
    vintage 590 days against a 180-day *polling* interval."""
    assert freshness_status(590 * 86400, max_vintage_seconds=None) == NOT_CONFIGURED


def test_a_declared_tolerance_is_compared():
    assert freshness_status(10.0, max_vintage_seconds=100.0) == FRESH
    assert freshness_status(101.0, max_vintage_seconds=100.0) == STALE


def test_the_boundary_is_not_stale():
    """`>` and `>=` are equally plausible; a source polled on exactly its own
    cadence would otherwise be stale on every run."""
    assert freshness_status(100.0, max_vintage_seconds=100.0) == FRESH


def test_a_declared_tolerance_with_no_measurement_is_unknown():
    assert freshness_status(None, max_vintage_seconds=100.0) == FRESHNESS_UNKNOWN


def test_no_axis_outranks_everything():
    """A source whose rows carry no observation time has no vintage to compare,
    and no tolerance would produce one."""
    for max_v in (None, 100.0):
        for vintage in (None, 1.0):
            assert freshness_status(vintage, has_axis=False,
                                    max_vintage_seconds=max_v) == FRESHNESS_NOT_APPLICABLE


def test_a_missing_tolerance_outranks_a_missing_measurement():
    """Both leave age out of the decision, and they are different things to fix.

    Reporting "nobody agreed a threshold" as "the query came back empty" sends
    someone to look at the database.
    """
    assert freshness_status(None, max_vintage_seconds=None) == NOT_CONFIGURED
