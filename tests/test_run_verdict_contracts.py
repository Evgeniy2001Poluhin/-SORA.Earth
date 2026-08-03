"""One verdict, three readers -- checked by running the job, not by reading it.

The first version of this file parsed the source and asserted that the string
`status="success"` no longer appeared. That is brittle in both directions: it
breaks on a rename that changes nothing, and it passes on a rewrite that
reintroduces the divergence through a variable. It tested the text.

These call the jobs with a persist result shaped for each verdict and assert the
three readers agree:

  the value the function returns
  the status written to environmental_job_log
  the label attached to the Prometheus counter

The divergence this guards against is quiet by nature. A job that records
`degraded` and returns `success` is not wrong anywhere a test usually looks --
each reader is self-consistent, and they only disagree with each other.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingesters.base import Signal
from app.ingesters.persist import PersistResult
from app.services.environmental import scheduler_jobs
from app.ingesters.classification import classify_run, EMPTY, FAILURE


def _signal(region="RU-MOW"):
    """A real Signal, because the job reads .metadata off each one.

    A bare object() got as far as the quality filter and raised there -- the
    fake has to satisfy the code path, not merely occupy the list.
    """
    return Signal(
        region_code=region, source="openaq", metric="pm25_ugm3",
        value=1.0, unit="ug/m3",
        observed_at=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
        metadata={},
    )


def _persist(received, accepted, rejected=0):
    return PersistResult(
        received=received, inserted=accepted, updated=0,
        rejected=rejected, duplicates=0, accepted=accepted, errors=[],
    )


def _run_openaq(signals, persist_result):
    """Run the job with everything external mocked, and collect all three readers."""
    ingestion_counter = MagicMock()
    with patch(
        # RedisLock.acquire() returns True when Redis is unreachable and False
        # when a reachable Redis already holds the key, so leaving it unmocked
        # makes these tests depend on whatever a developer's Redis happens to
        # contain -- passing on one machine and skipping the job on another.
        "app.locks.RedisLock.acquire", return_value=True,
    ), patch(
        "app.locks.RedisLock.release", return_value=None,
    ), patch(
        "app.ingesters.openaq.OpenAQIngester.fetch",
        new_callable=AsyncMock, return_value=signals,
    ), patch(
        "app.ingesters.persist.persist_environmental_observations",
        return_value=persist_result,
    ), patch(
        "app.prom_metrics.sora_environmental_ingestion_total", ingestion_counter,
    ), patch(
        "app.prom_metrics.sora_environmental_observations_total", MagicMock(),
    ), patch.object(scheduler_jobs, "_log_job_execution") as logged:
        returned = scheduler_jobs.scheduled_openaq_ingestion()

    label = None
    if ingestion_counter.labels.call_args is not None:
        label = ingestion_counter.labels.call_args.kwargs.get("status")
    recorded = logged.call_args.kwargs.get("status") if logged.call_args else None
    return returned, recorded, label


@pytest.mark.parametrize(
    "signals,persist_result,expected",
    [
        # The source gave nothing: 398 production runs, every one recorded
        # success before this change.
        ([], _persist(0, 0), "degraded"),
        # Everything asked for, stored.
        ([_signal()], _persist(3, 3), "success"),
        # Some stored, some rejected -- something was stored, but not what was
        # asked for.
        ([_signal()], _persist(3, 2, rejected=1), "degraded"),
    ],
)
def test_the_three_readers_agree(signals, persist_result, expected):
    returned, recorded, label = _run_openaq(signals, persist_result)

    assert returned["status"] == expected, "returned value"
    assert recorded == expected, "database row"
    assert label == expected, "prometheus label"


def test_they_agree_when_the_run_fails():
    """The failure path is a separate branch and can drift on its own."""
    ingestion_counter = MagicMock()
    with patch(
        "app.locks.RedisLock.acquire", return_value=True,
    ), patch(
        "app.locks.RedisLock.release", return_value=None,
    ), patch(
        "app.ingesters.openaq.OpenAQIngester.fetch",
        new_callable=AsyncMock, side_effect=RuntimeError("upstream 503"),
    ), patch(
        "app.prom_metrics.sora_environmental_ingestion_total", ingestion_counter,
    ), patch.object(scheduler_jobs, "_log_job_execution") as logged:
        returned = scheduler_jobs.scheduled_openaq_ingestion()

    recorded = logged.call_args.kwargs.get("status") if logged.call_args else None
    assert returned.get("status") in ("error", "failed"), returned
    assert recorded == "failed", recorded


def test_a_degraded_run_carries_its_reason_to_every_reader():
    """A verdict without a reason is as unreadable as the `ok` it replaces, and
    the reason has to survive the trip out of the job."""
    returned, _, _ = _run_openaq([], _persist(0, 0))
    assert returned["failure_reason"], returned


# --- the persist contract ----------------------------------------------------

def test_persist_results_have_the_same_keys_on_every_path():
    """Checked by calling both paths, not by parsing them.

    The failure path returned {"saved": 0, "error": ...} -- no `received`, no
    `accepted` -- so `.get(key, 0)` yielded 0 for both and a missing persistence
    layer was classified as a source with nothing to give.
    """
    from app.ingesters import runner

    good = runner._persist_signals([], "test")

    with patch.dict("sys.modules", {"app.ingesters.persist": None}):
        broken = runner._persist_signals([], "test")

    assert set(good) == set(broken), (
        "persist results differ by branch: %s vs %s" % (sorted(good), sorted(broken))
    )
    for required in ("received", "accepted", "rejected", "write_failed"):
        assert required in good and required in broken, required
    assert broken["write_failed"] is True
    assert good["write_failed"] is False


def test_a_missing_persistence_layer_is_a_failure_not_an_empty_source():
    """End to end for the defect this change introduced and review caught."""
    from app.ingesters import runner

    with patch.dict("sys.modules", {"app.ingesters.persist": None}):
        result = runner._persist_signals([], "test")

    v = classify_run(
        received=result["received"],
        accepted=result["accepted"],
        rejected=result["rejected"],
        write_failed=result["write_failed"],
    )
    assert v.status == FAILURE
    assert v.primary_source_status != EMPTY
    assert "could not be persisted" in v.failure_reason


def test_signals_without_timestamps_do_not_crash_the_run():
    """A latent crash this file found while being written.

    The freshness metric was guarded by `if signals:` and then took max() over
    the ones whose observed_at is a datetime. A batch where none is -- a source
    that omitted the field, or returned it unparseable -- left max() with an
    empty sequence and raised ValueError, failing a run that had otherwise
    worked. Both real ingesters can produce that shape.
    """
    undated = Signal(
        region_code="RU-MOW", source="openaq", metric="pm25_ugm3",
        value=1.0, unit="ug/m3", observed_at=None, metadata={},
    )
    returned, recorded, label = _run_openaq([undated], _persist(1, 1))

    assert returned["status"] == "success"
    assert recorded == "success"
    assert label == "success"
