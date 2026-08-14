"""A point observation records where it was taken.

Issue #84. `latitude` and `longitude` were NULL on every row of every source --
24,140 of 24,140 for openmeteo and the same for the rest -- while the columns
existed and both geographic ingesters knew where they were reading. A canary
check failed against a rule the platform had never once satisfied.

Two ends had to meet, and only testing both catches either:

    openmeteo          sent coordinates to the API and put nothing in the
                       Signal, so there was nothing for persist to carry
    persist            wrote `None` with a TODO beside it, so a Signal that
                       did carry them (openmeteo_air_quality) lost them anyway

NULL keeps a meaning afterwards, which is why the refusals are tested as
carefully as the writes: rosstat and sber_veb_baseline publish region-level
figures that are not taken anywhere. A coordinate invented for them would be a
claim about where a number was measured.
"""
from datetime import datetime, timezone

import pytest

from app.ingesters.base import Signal
from app.ingesters.persist import _coordinates, _signal_to_observation_dict

MOSCOW = (55.7558, 37.6173)
OBSERVED_AT = datetime(2026, 8, 14, 3, 0, tzinfo=timezone.utc)


def _signal(metadata, kind="observed"):
    """`observed_at` is not optional here.

    The temporal contract refuses an `observed` row with no observation time
    rather than filling one in, which is what wrote `now` onto 12,240 rows
    before #121. A fixture that omitted it was refused by the code under test
    -- correctly -- and that is worth leaving visible.
    """
    return Signal(
        region_code="RU-MOW",
        source="openmeteo",
        metric="temperature",
        value=12.5,
        unit="C",
        observed_at=OBSERVED_AT if kind == "observed" else None,
        temporal_kind=kind,
        source_revision=None if kind == "observed" else "rev:v1:" + "a" * 64,
        metadata=metadata,
    )


# --- what reaches the columns ------------------------------------------------


def test_a_signal_carrying_a_point_writes_it_to_the_columns():
    lat, lon = MOSCOW
    row = _signal_to_observation_dict(
        _signal({"latitude": lat, "longitude": lon}), "openmeteo")

    assert row["latitude"] == pytest.approx(lat)
    assert row["longitude"] == pytest.approx(lon)


def test_a_source_with_no_metadata_leaves_them_absent():
    """rosstat and sber_veb_baseline. Absent is the honest answer."""
    row = _signal_to_observation_dict(
        _signal(None, kind="not_applicable"), "rosstat")

    assert row["latitude"] is None
    assert row["longitude"] is None


def test_metadata_without_coordinates_leaves_them_absent():
    row = _signal_to_observation_dict(
        _signal({"measurement_kind": "modelled"}), "openmeteo_air_quality")

    assert row["latitude"] is None
    assert row["longitude"] is None


def test_the_rest_of_the_metadata_survives():
    """Reading the coordinates out must not consume them or anything else.

    `measurement_kind` is the field #57 exists to preserve, and it travels in
    the same dict.
    """
    row = _signal_to_observation_dict(
        _signal({"latitude": 55.0, "longitude": 37.0,
                 "measurement_kind": "modelled"}),
        "openmeteo_air_quality")

    assert "modelled" in row["metadata_json"]
    assert "latitude" in row["metadata_json"]


# --- what must not ----------------------------------------------------------


@pytest.mark.parametrize("metadata", [
    {"latitude": 55.7558},
    {"longitude": 37.6173},
])
def test_one_coordinate_without_the_other_is_not_a_location(metadata):
    """Storing the half that arrived puts the row on the equator or the prime
    meridian, and both are places."""
    assert _coordinates(metadata) == (None, None)


@pytest.mark.parametrize("lat,lon", [
    (91.0, 37.0),
    (-90.5, 37.0),
    (55.0, 181.0),
    (55.0, -180.5),
])
def test_a_value_outside_the_range_is_refused_not_clamped(lat, lon):
    """Clamping moves a broken reading onto a real point on the map, where
    nothing distinguishes it from a good one."""
    assert _coordinates({"latitude": lat, "longitude": lon}) == (None, None)


@pytest.mark.parametrize("lat,lon", [
    ("unknown", 37.0),
    (55.0, None),
    ([55.0], 37.0),
    ({}, 37.0),
])
def test_a_non_numeric_coordinate_is_refused(lat, lon):
    assert _coordinates({"latitude": lat, "longitude": lon}) == (None, None)


def test_nan_is_refused():
    """NaN fails every comparison, so a range check written as `not (lat < -90
    or lat > 90)` lets it through. Asked positively it does not."""
    assert _coordinates({"latitude": float("nan"), "longitude": 37.0}) == (None, None)


def test_the_boundaries_themselves_are_accepted():
    """The poles and the antimeridian are coordinates.

    A range check written with `<` and `>` at the wrong end silently drops
    them, and nothing else in this file would notice.
    """
    assert _coordinates({"latitude": 90.0, "longitude": 180.0}) == (90.0, 180.0)
    assert _coordinates({"latitude": -90.0, "longitude": -180.0}) == (-90.0, -180.0)


def test_zero_is_a_coordinate_not_an_absence():
    """Null Island is a real point, and `if not lat` would discard it.

    The bug this guards is one line of falsiness away in any rewrite.
    """
    assert _coordinates({"latitude": 0.0, "longitude": 0.0}) == (0.0, 0.0)


# --- the ingester end -------------------------------------------------------


def test_openmeteo_attaches_the_point_it_asked_the_api_about():
    """The source of the 24,140 rows that carry nothing.

    Read off the module rather than restated: the coordinates a row should
    carry are the ones sent to the API, and REGION_CAPITALS is where both come
    from. A test with its own copy of Moscow would pass while the ingester sent
    a request somewhere else.
    """
    from app.ingesters.openmeteo import REGION_CAPITALS

    assert REGION_CAPITALS, "no capitals declared"
    for entry in REGION_CAPITALS:
        code, lat, lon = entry
        assert _coordinates({"latitude": lat, "longitude": lon}) == (lat, lon), (
            f"{code} declares a point the persist layer would refuse: {lat},{lon}"
        )
