"""OpenAQ is stood down, and the reason must survive being written down.

Every station within 25km of the 21 declared regions stopped reporting in
September 2017 (#57), measured with a working API key and HTTP 200 throughout.
Running the ingester hourly spends 21 requests to produce nothing and posts a
`degraded` line four times a day -- a warning that is normal every hour, which
nobody reads.

Two things this must not become:

  - a claim that Open-Meteo *replaces* it. Open-Meteo Air Quality is a CAMS
    reanalysis: a model's estimate of an air quality, not an instrument's
    reading of one. It covers the same coordinates and not the same question,
    and the register has to keep saying so.
  - a deletion. The adapter, the v3 parser and its tests (#117, #119) stay,
    and so do the historical rows.
"""
import importlib

import pytest

from app.ingesters.source_register import (
    MEASURED, MODELLED, SOURCE_REGISTER, STATUS_ACTIVE,
    STATUS_DISABLED_NO_CURRENT_STATIONS, openaq_enabled,
)


def test_openaq_is_off_unless_explicitly_enabled(monkeypatch):
    monkeypatch.delenv("SORA_OPENAQ_ENABLED", raising=False)
    assert openaq_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "enabled"])
def test_it_can_be_turned_on(monkeypatch, value):
    """The default must be overridable, or the flag is decoration."""
    monkeypatch.setenv("SORA_OPENAQ_ENABLED", value)
    assert openaq_enabled() is True


@pytest.mark.parametrize("value", ["", "off", "false", "no", "0", "maybe"])
def test_anything_else_leaves_it_off(monkeypatch, value):
    monkeypatch.setenv("SORA_OPENAQ_ENABLED", value)
    assert openaq_enabled() is False


def test_the_disabled_run_reports_its_own_status_not_a_failure(monkeypatch):
    """`degraded` every hour is indistinguishable from a source that broke."""
    monkeypatch.delenv("SORA_OPENAQ_ENABLED", raising=False)
    jobs = importlib.import_module("app.services.environmental.scheduler_jobs")

    result = jobs.scheduled_openaq_ingestion()

    assert result["status"] == STATUS_DISABLED_NO_CURRENT_STATIONS
    assert result["status"] != "degraded"
    assert result["records_processed"] == 0
    # The run says why, so an operator reading one line does not have to find
    # the issue that decided it.
    assert result["last_verified_data"] == "2017-09-11"
    assert "station" in result["reenable_condition"]


def test_the_disabled_run_makes_no_request(monkeypatch):
    """Standing a source down must actually stop the traffic.

    Without this the flag could report a status while the fetch still ran,
    which is the shape of every defect in this milestone.
    """
    monkeypatch.delenv("SORA_OPENAQ_ENABLED", raising=False)
    jobs = importlib.import_module("app.services.environmental.scheduler_jobs")

    called = []
    monkeypatch.setattr(jobs, "_run_ingestion",
                        lambda *a, **k: called.append(1) or {})

    jobs.scheduled_openaq_ingestion()

    assert called == [], "the ingestion path ran while the source was stood down"


def test_measured_and_modelled_are_not_conflated():
    """The distinction the register exists for.

    Open-Meteo has data where the stations are dead precisely because it is a
    model. Recording it as a replacement would make the substitution invisible
    in the number, which is worse than having neither.
    """
    assert SOURCE_REGISTER["openaq"].measurement_kind == MEASURED
    assert SOURCE_REGISTER["openmeteo_air_quality"].measurement_kind == MODELLED
    assert SOURCE_REGISTER["openmeteo"].measurement_kind == MODELLED

    # The register must say the two are different, not merely avoid a word.
    # An earlier version of this test banned the substring "replac" and failed
    # on the sentence that states the distinction -- a check that could only
    # be satisfied by not discussing the subject.
    aq = SOURCE_REGISTER["openmeteo_air_quality"]
    assert "modelled" in aq.notes or "model" in aq.notes
    assert aq.measurement_kind != SOURCE_REGISTER["openaq"].measurement_kind, (
        "the source that has data and the source that measures are not the "
        "same kind, and the register is where that stays visible"
    )


def test_the_register_records_why_openaq_is_down_and_what_would_undo_it():
    facts = SOURCE_REGISTER["openaq"]

    assert facts.status == STATUS_DISABLED_NO_CURRENT_STATIONS
    assert facts.last_verified_data == "2017-09-11"
    assert facts.coverage == "none for the declared regions"
    assert facts.reenable_condition, (
        "a stood-down source without a stated condition is one nobody can "
        "ever justify turning back on"
    )
    assert facts.requires_api_key is False


def test_the_live_sources_are_still_active():
    """The status must be able to be something else, or it asserts nothing."""
    assert SOURCE_REGISTER["openmeteo"].status == STATUS_ACTIVE
    assert SOURCE_REGISTER["openmeteo_air_quality"].status == STATUS_ACTIVE


def test_the_parser_and_its_tests_are_kept():
    """Stood down is not deleted: a future source on this endpoint inherits it."""
    from app.ingesters.openaq import OpenAQIngester, _sensor_parameter_map

    assert OpenAQIngester is not None
    assert _sensor_parameter_map({"sensors": [
        {"id": 1, "parameter": {"name": "pm25", "units": "µg/m³"}}
    ]}) == {1: {"name": "pm25", "units": "µg/m³"}}
