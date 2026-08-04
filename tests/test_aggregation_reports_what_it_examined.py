"""The aggregation job must say what it looked at, and notice an empty window.

393 production runs recorded `records_processed = 0` with `status = success`.
The column was simply never passed, so the zero meant "nobody filled this in"
while reading as "processed nothing" -- the same ambiguity that let openaq
report success 398 times over an empty source (#56, #69).

Two properties, and they are separate: the count must be real, and a window with
nothing in it must not look like a window that was aggregated successfully.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.services.environmental import scheduler_jobs


def _run(counts):
    """Drive the job with a stubbed database returning the given per-source counts.

    `counts` is [(observations, valid), ...] consumed in the order the job
    queries its sources.
    """
    scalars = []
    for obs, valid in counts:
        scalars.extend([obs, valid])

    query = MagicMock()
    query.filter.return_value = query
    query.scalar.side_effect = scalars

    session = MagicMock()
    session.query.return_value = query

    # Patched where it is imported from, not on the module under test:
    # SessionLocal is pulled in inside the function, so the name never exists as
    # a scheduler_jobs attribute and patch.object refuses it.
    with patch("app.database.SessionLocal", return_value=session), \
         patch("app.locks.RedisLock.acquire", return_value=True), \
         patch("app.locks.RedisLock.release", return_value=None), \
         patch.object(scheduler_jobs, "_log_job_execution") as logged:
        result = scheduler_jobs.scheduled_data_quality_aggregation()

    recorded = logged.call_args.kwargs if logged.call_args else {}
    return result, recorded


def test_it_records_the_number_of_observations_it_examined():
    result, recorded = _run([(120, 118), (80, 80)])

    assert result["records_processed"] == 200
    assert recorded["records_processed"] == 200, "the database row must agree"
    assert result["status"] == "success"
    assert recorded["status"] == "success"


def test_an_empty_window_is_degraded_not_a_success():
    """Every source silent for 24 hours is a finding.

    Reported as success it is indistinguishable from a run that had data, which
    is the defect being removed rather than a smaller version of it.
    """
    result, recorded = _run([(0, 0), (0, 0)])

    assert result["status"] == "degraded"
    assert recorded["status"] == "degraded"
    assert result["records_processed"] == 0
    assert "no observations" in result["failure_reason"]
    assert recorded["error_message"], "the row must carry the reason too"


def test_one_silent_source_does_not_degrade_the_run():
    """Degraded means nothing at all to aggregate.

    A single quiet source is a fact for the quality statistics to carry, not a
    verdict on the aggregation, which did its job over what existed.
    """
    result, _ = _run([(0, 0), (50, 49)])

    assert result["status"] == "success"
    assert result["records_processed"] == 50


@pytest.mark.parametrize("counts,expected", [
    ([(0, 0), (0, 0)], 0),
    ([(1, 0), (0, 0)], 1),
    ([(10, 10), (10, 0)], 20),
])
def test_the_count_is_observations_examined_not_observations_valid(counts, expected):
    """Enumerated, because "processed" could plausibly mean either and the two
    diverge exactly when quality is poor -- the case anyone reading this column
    is most likely to be investigating."""
    result, _ = _run(counts)
    assert result["records_processed"] == expected
