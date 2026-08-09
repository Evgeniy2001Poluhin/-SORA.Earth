"""The air-quality job, and that it runs beside OpenAQ rather than instead of it.

OpenAQ's stations for these regions stopped reporting in September 2017, so it
has produced nothing across hundreds of runs (#56, #57). The temptation is to
replace it. That would swap one gap for another nobody has looked at: the new
source is modelled and the old one was measured, and until the two have been
compared on real production data there is no evidence about how they differ.

So the property tested here is not "air quality is ingested". It is "air quality
is ingested **and OpenAQ still is**".
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.ingesters.base import Signal
from app.ingesters.persist import PersistResult


def _persist(received=6, accepted=6, rejected=0):
    return PersistResult(
        received=received, inserted=accepted, updated=0,
        rejected=rejected, duplicates=0, accepted=accepted, errors=[],
    )


def _signal(metric="pm2_5", value=21.7):
    return Signal(
        region_code="RUS", source="openmeteo_air_quality", metric=metric,
        value=value, unit="µg/m³", observed_at=datetime.now(timezone.utc),
        metadata={"measurement_kind": "modelled"},
    )


def test_the_job_persists_what_it_fetched():
    from app.services.environmental import scheduler_jobs

    signals = [_signal("pm2_5"), _signal("pm10", 34.5)]
    with patch(
        "app.ingesters.openmeteo_air_quality.OpenMeteoAirQualityIngester.fetch",
        new_callable=AsyncMock, return_value=signals,
    ), patch(
        "app.ingesters.persist.persist_environmental_observations",
        return_value=_persist(2, 2),
    ) as persist, patch.object(
        scheduler_jobs, "_log_job_execution"
    ), patch("app.locks.RedisLock", MagicMock()):
        result = scheduler_jobs.scheduled_openmeteo_air_quality_ingestion()

    persist.assert_called_once()
    assert persist.call_args[0][0] == signals
    # The source name is what every row is written under and what the metrics
    # are labelled with. A typo here separates the data from itself.
    assert persist.call_args[0][1] == "openmeteo_air_quality"
    assert result["status"] == "success"
    assert result["persisted"] == 2


def test_a_source_that_returns_nothing_is_not_a_success():
    """The defect that produced 333 successful runs with zero records.

    Reachable and empty is not the same as working, and the job must not report
    it as such — for this source least of all, since it exists because another
    one was silently empty.
    """
    from app.services.environmental import scheduler_jobs

    with patch(
        "app.ingesters.openmeteo_air_quality.OpenMeteoAirQualityIngester.fetch",
        new_callable=AsyncMock, return_value=[],
    ), patch(
        "app.ingesters.persist.persist_environmental_observations",
        return_value=_persist(0, 0),
    ), patch.object(
        scheduler_jobs, "_log_job_execution"
    ) as logged, patch("app.locks.RedisLock", MagicMock()):
        result = scheduler_jobs.scheduled_openmeteo_air_quality_ingestion()

    assert result["status"] == "degraded"
    assert result["failure_reason"]
    # The row and the return value must agree. They drifted apart before: the
    # log said degraded while the caller was handed "success".
    assert logged.call_args.kwargs["status"] == "degraded"


def test_it_runs_immediately_on_startup():
    """Otherwise the first rows arrive an hour after a deployment, and a restart
    to check the source is working shows nothing for an hour."""
    import inspect
    from app import scheduler as scheduler_module

    src = inspect.getsource(scheduler_module)
    # The tuple itself, delimited by the loop that consumes it. Slicing from the
    # first occurrence of a job id instead picked up the add_job() calls further
    # up, where every id appears -- so the assertion held with the job absent
    # from the immediate-run set entirely.
    start = src.index("for _jid in (")
    immediate = src[start:src.index("):", start)]
    assert '"auto_openmeteo_air_quality_ingestion"' in immediate, \
        "the air-quality job is not in the immediate-run set"
    # auto_openaq_ingestion is deliberately absent since #57: the job is not
    # registered unless SORA_OPENAQ_ENABLED is set, and an immediate run of a
    # job that does not exist is not something to arrange.
    assert '"auto_openaq_ingestion"' not in immediate


def test_a_write_failure_fails_the_job():
    from app.services.environmental import scheduler_jobs

    broken = PersistResult(
        received=6, inserted=0, updated=0, rejected=0, duplicates=0,
        accepted=0, errors=["persist_error: connection refused"],
    )
    import pytest
    with patch(
        "app.ingesters.openmeteo_air_quality.OpenMeteoAirQualityIngester.fetch",
        new_callable=AsyncMock, return_value=[_signal()],
    ), patch(
        "app.ingesters.persist.persist_environmental_observations",
        return_value=broken,
    ), patch.object(
        scheduler_jobs, "_log_job_execution"
    ) as logged, patch("app.locks.RedisLock", MagicMock()):
        with pytest.raises(RuntimeError):
            scheduler_jobs.scheduled_openmeteo_air_quality_ingestion()

    # Recorded before it is re-raised, so each attempt leaves a row.
    assert logged.call_args.kwargs["status"] == "failed"

    # Raised, not returned. `@with_retry` retries what raises, so a job that
    # hands back {"status": "error"} makes the decorator inert -- one attempt
    # while the annotation promises three. A broken database must also not be
    # classified as an empty source: one calls for a retry, the other for
    # someone to look at the source.


def test_signals_without_a_timestamp_do_not_fail_the_run():
    """A batch where nothing carries a datetime left max() with an empty
    sequence and raised, failing a run that had otherwise succeeded."""
    from app.services.environmental import scheduler_jobs

    undated = Signal(
        region_code="RUS", source="openmeteo_air_quality", metric="pm2_5",
        value=21.7, unit="µg/m³", observed_at=None, metadata={},
    )
    with patch(
        "app.ingesters.openmeteo_air_quality.OpenMeteoAirQualityIngester.fetch",
        new_callable=AsyncMock, return_value=[undated],
    ), patch(
        "app.ingesters.persist.persist_environmental_observations",
        return_value=_persist(1, 1),
    ), patch.object(
        scheduler_jobs, "_log_job_execution"
    ), patch("app.locks.RedisLock", MagicMock()):
        result = scheduler_jobs.scheduled_openmeteo_air_quality_ingestion()

    assert result["status"] == "success"


def test_the_comparison_openaq_was_kept_for_cannot_be_made():
    """This asserted the two sources run in parallel "until they have been
    compared". That requirement is discharged, not ignored, and the difference
    matters enough to write down.

    The comparison was to be measured against modelled on real production data.
    It cannot be made: OpenAQ's stations for all 21 declared regions stopped
    reporting in September 2017, so there is no measured series to compare a
    modelled one against. Running an hourly job that returns nothing does not
    move that forward -- 24 degraded runs a day for as long as it is scheduled,
    and never a row (#57).

    So OpenAQ is stood down from scheduling while everything needed to compare
    remains in place: the adapter, the v3 parser, its tests, and a re-enable
    condition stated in advance. What is gone is the pretence that leaving a
    silent job running constitutes waiting for evidence.

    The air-quality job must still be registered, and the register must still
    say the two are different kinds -- that is what stops a later edit from
    "cleaning up" the distinction along with the schedule.
    """
    import inspect
    from app import scheduler as scheduler_module
    from app.ingesters.source_register import SOURCE_REGISTER

    src = inspect.getsource(scheduler_module)
    assert 'id="auto_openmeteo_air_quality_ingestion"' in src, \
        "the air-quality job is not registered with the scheduler"
    assert 'id="auto_openmeteo_ingestion"' in src, \
        "the weather job was removed"

    # Registered only behind the gate, never unconditionally.
    assert 'id="auto_openaq_ingestion"' in src, \
        "the OpenAQ adapter was removed; it is stood down, not deleted"
    assert "openaq_scheduling_refusal" in src, \
        "OpenAQ is registered without the gate that stands it down"

    aq = SOURCE_REGISTER["openaq"]
    assert aq.status == "disabled_no_current_stations"
    assert aq.measurement_kind != SOURCE_REGISTER["openmeteo_air_quality"].measurement_kind, \
        "the modelled source has been recorded as the measured one"
    assert aq.reenable_condition, \
        "stood down with no stated way back is removal by another name"
