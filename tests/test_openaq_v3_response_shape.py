"""The openaq ingester must parse the response the endpoint actually returns.

`GET /v3/locations/{id}/latest` returns items with exactly these fields:

    datetime {utc, local} | value | coordinates | sensorsId | locationsId

The ingester read `measurement["parameter"]["name"]`, which is not among them:
the pollutant is a property of the *sensor*, reachable through `sensorsId`. So
`param` was always `""`, never in PARAMETERS, and every measurement was
discarded. Production: HTTP 200 from every request, 21 locations resolved,
**0 signals**, on every run since the ingester was written (#117).

The existing suite passed 20/20 throughout, because it fed
`{"parameter": {"name": "pm25"}, ...}` -- a shape the API has never produced.
A test carrying its own copy of the response cannot discover that the real one
differs, which is the whole reason these exist separately: they assert against
the documented shape, and the first one fails if the old reading returns.

Shape source: https://docs.openaq.org/resources/latest
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.ingesters.openaq import OpenAQIngester, _sensor_parameter_map


SENSOR_ID = 3920
LOCATION = {
    "id": 155,
    "name": "Test Location",
    "sensors": [
        {"id": SENSOR_ID, "name": "pm25 µg/m³",
         "parameter": {"id": 2, "name": "pm25", "units": "µg/m³",
                       "displayName": "PM2.5"}},
        {"id": 4044, "name": "no2 µg/m³",
         "parameter": {"id": 5, "name": "no2", "units": "µg/m³",
                       "displayName": "NO₂"}},
    ],
}


@pytest.fixture
def ingester():
    return OpenAQIngester()


def _latest(value=25.0, when=None, sensors_id=SENSOR_ID, locations_id=155):
    """One item exactly as /v3/locations/{id}/latest documents it."""
    when = when or datetime.now(timezone.utc)
    return {
        "datetime": {"utc": when.isoformat(), "local": when.isoformat()},
        "value": value,
        "coordinates": {"latitude": 38.9, "longitude": -77.0},
        "sensorsId": sensors_id,
        "locationsId": locations_id,
    }


def test_the_documented_shape_produces_a_signal(ingester):
    """The bug in one assertion: this returned None for every record."""
    now = datetime.now(timezone.utc)
    signal = ingester._process_measurement(
        _latest(when=now - timedelta(minutes=10)), "USA", now,
        _sensor_parameter_map(LOCATION),
    )

    assert signal is not None, "a documented v3 latest record must be parseable"
    assert signal.metric == "pm25_ugm3"
    assert signal.value == 25.0


def test_without_a_sensor_map_nothing_is_produced(ingester):
    """This is the production failure exactly.

    A 200 response, a well-formed record, and no way to name the pollutant --
    so it is dropped, silently, and the run reports zero from a healthy source.
    """
    now = datetime.now(timezone.utc)
    assert ingester._process_measurement(_latest(), "USA", now, {}) is None


def test_the_old_v2_shape_no_longer_smuggles_data_through(ingester):
    """Guards the direction of the fix.

    A record carrying `parameter.name` but no usable `sensorsId` must not
    produce a signal: if it did, the parser would still be reading a field the
    endpoint does not return, and the suite would go green on fiction again.
    """
    now = datetime.now(timezone.utc)
    v2_shaped = {
        "parameter": {"name": "pm25"},
        "value": 25.0,
        "unit": "µg/m³",
        "locationId": "loc123",
        "period": {"datetimeLast": {"utc": now.isoformat()}},
    }

    assert ingester._process_measurement(
        v2_shaped, "USA", now, _sensor_parameter_map(LOCATION)
    ) is None


def test_an_unknown_sensor_is_skipped(ingester):
    now = datetime.now(timezone.utc)
    assert ingester._process_measurement(
        _latest(sensors_id=999999), "USA", now, _sensor_parameter_map(LOCATION)
    ) is None


def test_the_timestamp_comes_from_datetime_utc(ingester):
    """Not from `period.datetimeLast`, which the endpoint does not return.

    Without this the observation time silently became "now", making stale data
    indistinguishable from fresh.
    """
    now = datetime.now(timezone.utc)
    observed = now - timedelta(hours=6)

    signal = ingester._process_measurement(
        _latest(when=observed), "USA", now, _sensor_parameter_map(LOCATION),
    )

    assert signal is not None
    assert abs(signal.metadata["data_age_hours"] - 6.0) < 0.05
    assert "old_data" in " ".join(signal.metadata["quality_issues"])


def test_locations_id_is_read_so_completeness_does_not_misfire(ingester):
    """`locationId` never exists in v3, so the check flagged every record."""
    now = datetime.now(timezone.utc)

    quality, issues = ingester._validate_measurement(
        "pm25", 25.0, now, now, _latest(), "µg/m³"
    )

    assert "missing_location_id" not in issues
    assert issues == []


def test_a_record_with_no_locations_id_is_still_flagged(ingester):
    """The completeness check must remain able to fire, or it asserts nothing."""
    now = datetime.now(timezone.utc)
    record = _latest()
    del record["locationsId"]

    _quality, issues = ingester._validate_measurement(
        "pm25", 25.0, now, now, record, "µg/m³"
    )

    assert "missing_location_id" in issues


def test_the_unit_comes_from_the_sensor(ingester):
    """A v3 latest record carries no unit; it belongs to the sensor.

    Read off the measurement, `actual_unit` was always None and the mismatch
    check could never fire.
    """
    now = datetime.now(timezone.utc)

    _quality, issues = ingester._validate_measurement(
        "pm25", 25.0, now, now, _latest(), "ppm"
    )

    assert any("unit_mismatch" in i for i in issues)


def test_the_sensor_map_is_built_from_the_location_object(ingester):
    mapping = _sensor_parameter_map(LOCATION)

    assert mapping[SENSOR_ID] == {"name": "pm25", "units": "µg/m³"}
    assert mapping[4044]["name"] == "no2"
    assert len(mapping) == 2


def test_a_location_without_sensors_yields_an_empty_map():
    assert _sensor_parameter_map({"id": 1}) == {}
    assert _sensor_parameter_map({"id": 1, "sensors": []}) == {}
    assert _sensor_parameter_map({"id": 1, "sensors": [{"id": 7}]}) == {}


PPM_LOCATION = {
    "id": 155,
    "sensors": [
        {"id": 7001, "parameter": {"name": "co", "units": "ppm"}},
    ],
}


def test_a_reading_in_other_units_is_not_emitted(ingester):
    """It would be stored mislabelled, and inside the accepted range.

    Every range in PARAMETER_RANGES is calibrated for µg/m³ and every Signal is
    labelled `ug/m3`. CO in ppm is ~0.5-5, comfortably inside [0, 50000], so
    the range check passes and the value lands three orders of magnitude wrong
    under a unit it is not in.
    """
    now = datetime.now(timezone.utc)
    record = _latest(value=3.0, sensors_id=7001)

    signal = ingester._process_measurement(
        record, "USA", now, _sensor_parameter_map(PPM_LOCATION)
    )

    assert signal is None
    assert ingester.quality_stats["invalid"] == 1


def test_a_reading_in_the_expected_unit_is_emitted(ingester):
    """The refusal must be able to not fire, or it asserts nothing."""
    now = datetime.now(timezone.utc)

    signal = ingester._process_measurement(
        _latest(value=25.0), "USA", now, _sensor_parameter_map(LOCATION)
    )

    assert signal is not None
    assert signal.unit == "ug/m3"
    assert signal.metadata["unit_original"] == "µg/m³"
