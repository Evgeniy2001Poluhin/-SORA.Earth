"""Regression tests: scheduled ingestion jobs must persist fetched signals.

scheduled_openaq_ingestion() and scheduled_openmeteo_ingestion() used to fetch
Signal objects, compute Prometheus metrics / job-log stats from them, and then
discard them without ever calling persist_environmental_observations() — so the
hourly "primary" ingestion jobs never wrote a single row to
environmental_observations. These tests pin the fix: both jobs must call the
persist layer with the fetched signals and the correct source name.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.ingesters.base import Signal
from app.ingesters.persist import PersistResult


def _fake_persist_result(n=1):
    return PersistResult(
        received=n, inserted=n, updated=0,
        rejected=0, duplicates=0, accepted=n, errors=[]
    )


def test_scheduled_openaq_ingestion_persists_fetched_signals():
    from app.services.environmental import scheduler_jobs

    signal = Signal(
        region_code="DEU", source="openaq", metric="pm25_ugm3",
        value=12.3, unit="ug/m3", observed_at=datetime.now(timezone.utc),
        metadata={"quality": "excellent"},
    )

    with patch(
        "app.ingesters.openaq.OpenAQIngester.fetch",
        new_callable=AsyncMock, return_value=[signal],
    ), patch(
        "app.ingesters.persist.persist_environmental_observations",
        return_value=_fake_persist_result(1),
    ) as mock_persist, patch.object(
        scheduler_jobs, "_log_job_execution"
    ):
        result = scheduler_jobs.scheduled_openaq_ingestion()

    mock_persist.assert_called_once_with([signal], "openaq")
    assert result["status"] == "success"
    assert result["persisted"] == 1


def test_scheduled_openmeteo_ingestion_persists_fetched_signals():
    from app.services.environmental import scheduler_jobs

    signal = Signal(
        region_code="DEU", source="openmeteo", metric="temperature_c",
        value=21.0, unit="C", observed_at=datetime.now(timezone.utc),
    )

    with patch(
        "app.ingesters.openmeteo.OpenMeteoIngester.fetch",
        new_callable=AsyncMock, return_value=[signal],
    ), patch(
        "app.ingesters.persist.persist_environmental_observations",
        return_value=_fake_persist_result(1),
    ) as mock_persist, patch.object(
        scheduler_jobs, "_log_job_execution"
    ):
        result = scheduler_jobs.scheduled_openmeteo_ingestion()

    mock_persist.assert_called_once_with([signal], "openmeteo")
    assert result["status"] == "success"
    assert result["persisted"] == 1


def test_scheduled_openaq_ingestion_handles_no_signals():
    """Empty fetch must not error and must still report persisted=0."""
    from app.services.environmental import scheduler_jobs

    with patch(
        "app.ingesters.openaq.OpenAQIngester.fetch",
        new_callable=AsyncMock, return_value=[],
    ), patch(
        "app.ingesters.persist.persist_environmental_observations",
        return_value=_fake_persist_result(0),
    ) as mock_persist, patch.object(
        scheduler_jobs, "_log_job_execution"
    ):
        result = scheduler_jobs.scheduled_openaq_ingestion()

    mock_persist.assert_called_once_with([], "openaq")
    # Degraded, not success.
    #
    # This asserted "success" for a run that fetched nothing and stored nothing,
    # which is precisely the behaviour measured on production: 398 openaq runs,
    # 0 records, `success` every time. The test was not wrong about what the code
    # did -- it was pinning the defect in place.
    assert result["status"] == "degraded"
    assert result["persisted"] == 0
    assert "no records" in result["failure_reason"]


def test_runner_ingesters_ownership_excludes_hourly_owned_sources():
    """Ownership contract: OpenAQ and Open-Meteo are owned exclusively by their
    hourly scheduled_openaq_ingestion/scheduled_openmeteo_ingestion jobs; the
    24h runner (app/ingesters/runner.py) must only cover Rosstat and Sber/VEB,
    which have no other scheduled owner."""
    from app.ingesters.runner import INGESTERS
    from app.ingesters.rosstat import RosstatIngester
    from app.ingesters.sber_veb_baseline import SberVebBaselineIngester
    from app.ingesters.openaq import OpenAQIngester
    from app.ingesters.openmeteo import OpenMeteoIngester

    assert set(INGESTERS) == {RosstatIngester, SberVebBaselineIngester}
    assert OpenAQIngester not in INGESTERS
    assert OpenMeteoIngester not in INGESTERS


def test_accepted_indicator_counts_excludes_rejected_signal():
    """Pure-function check: a rejected signal (per its index in
    PersistResult.errors) must not contribute to the accepted-indicator counts
    used to drive sora_environmental_observations_total."""
    from app.services.environmental.scheduler_jobs import _accepted_indicator_counts

    now = datetime.now(timezone.utc)
    signals = [
        Signal(region_code="DEU", source="openaq", metric="pm25_ugm3", value=1.0,
               unit="ug/m3", observed_at=now),
        Signal(region_code="FRA", source="openaq", metric="pm25_ugm3", value=2.0,
               unit="ug/m3", observed_at=now),
        Signal(region_code="GBR", source="openaq", metric="pm10_ugm3", value=3.0,
               unit="ug/m3", observed_at=now),
    ]
    persist_result = PersistResult(
        received=3, inserted=2, updated=0, rejected=1, duplicates=0, accepted=2,
        errors=["signal_1: region_code required"],
    )

    counts = _accepted_indicator_counts(signals, persist_result)
    assert counts == {"pm25_ugm3": 1, "pm10_ugm3": 1}


def test_accepted_indicator_counts_empty_on_fatal_error():
    """On a fatal batch failure nothing was persisted, so the helper must
    return no counts regardless of what's in the signals list."""
    from app.services.environmental.scheduler_jobs import _accepted_indicator_counts

    signals = [
        Signal(region_code="DEU", source="openaq", metric="pm25_ugm3", value=1.0,
               unit="ug/m3", observed_at=datetime.now(timezone.utc)),
    ]
    persist_result = PersistResult(
        received=1, inserted=0, updated=0, rejected=0, duplicates=0, accepted=0,
        errors=["persist_error: connection reset"],
    )

    assert _accepted_indicator_counts(signals, persist_result) == {}


def test_accepted_indicator_counts_matches_real_persist_error_format():
    """End-to-end contract check: _accepted_indicator_counts' parsing of the
    "signal_<idx>: ..." format must match what persist_environmental_observations
    actually produces (app/ingesters/persist.py), not just an assumed format.
    Uses the real persist function (test_-prefixed source, cleaned up after)."""
    from app.services.environmental.scheduler_jobs import _accepted_indicator_counts
    from app.ingesters.persist import persist_environmental_observations
    from app.database import SessionLocal, EnvironmentalObservation

    now = datetime.now(timezone.utc)
    signals = [
        Signal(region_code="DEU", source="test_openaq", metric="pm25_ugm3", value=1.0,
               unit="ug/m3", observed_at=now),
        Signal(region_code="", source="test_openaq", metric="pm25_ugm3", value=2.0,
               unit="ug/m3", observed_at=now),  # invalid: empty region_code
        Signal(region_code="FRA", source="test_openaq", metric="pm10_ugm3", value=3.0,
               unit="ug/m3", observed_at=now),
    ]

    db = SessionLocal()
    try:
        db.query(EnvironmentalObservation).filter(
            EnvironmentalObservation.source == "test_openaq"
        ).delete()
        db.commit()

        persist_result = persist_environmental_observations(signals, "test_openaq")
        assert persist_result.rejected == 1
        assert persist_result.accepted == 2

        counts = _accepted_indicator_counts(signals, persist_result)
        assert counts == {"pm25_ugm3": 1, "pm10_ugm3": 1}
    finally:
        db.query(EnvironmentalObservation).filter(
            EnvironmentalObservation.source == "test_openaq"
        ).delete()
        db.commit()
        db.close()


def test_scheduled_openaq_ingestion_mixed_batch_observation_counters():
    """Integration check through the real job function: with 3 fetched
    signals where 1 is rejected at persist time, sora_environmental_observations_total
    must be incremented once per indicator using only the accepted signals —
    the rejected signal's indicator must not be double-counted."""
    from app.services.environmental import scheduler_jobs

    now = datetime.now(timezone.utc)
    signals = [
        Signal(region_code="DEU", source="openaq", metric="pm25_ugm3", value=25.5,
               unit="ug/m3", observed_at=now, metadata={"quality": "excellent"}),
        Signal(region_code="FRA", source="openaq", metric="pm25_ugm3", value=30.0,
               unit="ug/m3", observed_at=now, metadata={"quality": "excellent"}),
        Signal(region_code="GBR", source="openaq", metric="pm10_ugm3", value=42.0,
               unit="ug/m3", observed_at=now, metadata={"quality": "good"}),
    ]
    # Signal index 1 (FRA pm25_ugm3) rejected at persist time.
    persist_result = PersistResult(
        received=3, inserted=2, updated=0, rejected=1, duplicates=0, accepted=2,
        errors=["signal_1: region_code required"],
    )

    mock_observations_total = MagicMock()

    with patch(
        "app.ingesters.openaq.OpenAQIngester.fetch",
        new_callable=AsyncMock, return_value=signals,
    ), patch(
        "app.ingesters.persist.persist_environmental_observations",
        return_value=persist_result,
    ), patch(
        "app.prom_metrics.sora_environmental_observations_total", mock_observations_total,
    ), patch.object(
        scheduler_jobs, "_log_job_execution"
    ) as mock_log:
        result = scheduler_jobs.scheduled_openaq_ingestion()

    # Partial, so degraded: three records arrived and one was rejected.
    #
    # "success" here would mean the run achieved a complete result from the
    # source, which it did not -- and the whole point of the verdict is that
    # "something was stored" and "what was asked for was stored" are different
    # facts. The counters below already knew the difference; the status did not.
    assert result["status"] == "degraded"
    assert result["persisted"] == 2
    assert "1 of 3" in result["failure_reason"]

    labels_calls = mock_observations_total.labels.call_args_list
    inc_calls = mock_observations_total.labels.return_value.inc.call_args_list
    observed = {
        labels_calls[i].kwargs["indicator"]: inc_calls[i].args[0]
        for i in range(len(labels_calls))
    }
    # pm25_ugm3 must be 1 (only the accepted DEU signal), NOT 2 — the rejected
    # FRA pm25_ugm3 signal must not increment the counter.
    assert observed == {"pm25_ugm3": 1, "pm10_ugm3": 1}

    # records_processed/rejected must come from PersistResult, and fetched
    # count must be tracked separately in metadata.
    log_call = mock_log.call_args
    assert log_call.kwargs["records_processed"] == 2
    assert log_call.kwargs["records_rejected"] == 1
    assert log_call.kwargs["metadata"]["fetched_count"] == 3


def test_scheduled_openaq_ingestion_fatal_persist_error_fails_job():
    """A fatal batch-level persistence failure ("persist_error:" in
    PersistResult.errors) must: mark the job failed (not success), skip the
    success counter, increment the error counter, and still release the lock
    (via the existing try/except/finally — persistence failures are routed
    through the same failure path as a fetch error)."""
    from app.services.environmental import scheduler_jobs

    signal = Signal(
        region_code="DEU", source="openaq", metric="pm25_ugm3", value=1.0,
        unit="ug/m3", observed_at=datetime.now(timezone.utc),
    )
    fatal_result = PersistResult(
        received=1, inserted=0, updated=0, rejected=0, duplicates=0, accepted=0,
        errors=["persist_error: connection reset"],
    )

    mock_ingestion_total = MagicMock()
    mock_errors_total = MagicMock()
    mock_lock = MagicMock()
    mock_lock.acquire.return_value = True

    with patch(
        "app.ingesters.openaq.OpenAQIngester.fetch",
        new_callable=AsyncMock, return_value=[signal],
    ), patch(
        "app.ingesters.persist.persist_environmental_observations",
        return_value=fatal_result,
    ), patch(
        "app.prom_metrics.sora_environmental_ingestion_total", mock_ingestion_total,
    ), patch(
        "app.prom_metrics.sora_environmental_ingestion_errors_total", mock_errors_total,
    ), patch(
        "app.locks.RedisLock", return_value=mock_lock,
    ), patch.object(
        scheduler_jobs, "_log_job_execution"
    ) as mock_log:
        result = scheduler_jobs.scheduled_openaq_ingestion()

    assert result["status"] == "error"

    # Success status/metric must never be recorded for a fatal persist failure.
    for c in mock_ingestion_total.labels.call_args_list:
        assert c.kwargs.get("status") != "success"

    mock_errors_total.labels.assert_called_with(source="openaq")
    mock_errors_total.labels.return_value.inc.assert_called_once()

    mock_log.assert_called_once()
    assert mock_log.call_args.kwargs["status"] == "failed"

    mock_lock.release.assert_called_once()
