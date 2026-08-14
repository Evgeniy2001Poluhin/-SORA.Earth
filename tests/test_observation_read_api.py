"""What the ingesters wrote gets a reader, with its provenance intact.

#84. `environmental_observations` was written by three ingesters and read by no
API: 160 published endpoints, none about observations. So the one thing the
air-quality work took care to record -- that a CAMS reanalysis is a model's
estimate and not an instrument's reading -- had nobody to tell. The provenance
was correct and invisible.

The substance here is the measured/modelled boundary. A page of modelled
readings looks exactly like a page of measured ones: same shape, same units,
plausible numbers. Nothing in the rows themselves tells them apart, which is
why the filter and the declared kind are tested rather than the pagination.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.database import EnvironmentalObservation, SessionLocal

NOW = datetime(2026, 8, 14, 6, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def as_admin(client):
    from app.auth import require_admin
    from app.main import app

    app.dependency_overrides[require_admin] = lambda: {"username": "t", "role": "admin"}
    yield
    app.dependency_overrides.pop(require_admin, None)


@pytest.fixture
def rows():
    """Written directly, so the view is under test and not the ingesters."""
    db = SessionLocal()
    try:
        db.query(EnvironmentalObservation).delete()
        db.commit()
        yield db
        db.query(EnvironmentalObservation).delete()
        db.commit()
    finally:
        db.close()


def _add(db, source, indicator="pm2_5", *, region="RU-MOW", value=7.5,
         hours_ago=1, lat=None, lon=None, kind="observed", unit="ug/m3",
         metadata=None):
    db.add(EnvironmentalObservation(
        region_id=region,
        country_code="RU",
        latitude=lat,
        longitude=lon,
        indicator=indicator,
        value=value,
        unit=unit,
        source=source,
        source_record_id=f"{source}:{indicator}:{region}:{hours_ago}",
        temporal_kind=kind,
        event_time=(NOW - timedelta(hours=hours_ago)) if kind == "observed" else None,
        source_revision=None if kind == "observed" else "rev:v1:" + "a" * 64,
        ingested_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        is_valid=True,
        metadata_json=metadata,
    ))
    db.commit()


def _get(client, **params):
    """Through `params`, never string-concatenated.

    An ISO timestamp ends in `+00:00`, and a `+` in a hand-built query string
    decodes to a space -- so the request arrives as `...T04:00:00 00:00` and is
    rejected with 422. The failure looks like the endpoint refusing a valid
    time, which is a long way from where the mistake is.
    """
    return client.get(
        "/api/v1/observations",
        params={k: v for k, v in params.items() if v is not None},
    )


# --- the provenance the table was recording for nobody -----------------------


def test_a_modelled_row_says_so_and_names_the_model(client, rows):
    _add(rows, "openmeteo_air_quality")

    row = _get(client).json()["observations"][0]

    assert row["measurement_kind"] == "modelled"
    assert row["model"] == "CAMS reanalysis via Open-Meteo"


def test_a_modelled_source_that_names_no_model_says_None_not_a_guess(client, rows):
    """openmeteo calls /v1/forecast without `models=`, so Open-Meteo selects
    per location. There is no single name, and inventing one would be a claim
    the source never made."""
    _add(rows, "openmeteo", indicator="temperature", unit="C")

    row = _get(client).json()["observations"][0]

    assert row["measurement_kind"] == "modelled"
    assert row["model"] is None


def test_a_source_nobody_declared_is_not_given_a_kind(client, rows):
    """Rows under an undeclared name mean something wrote them unannounced.

    Defaulting to `measured` would be exactly the substitution the register
    exists to prevent, and it would be invisible in the number.
    """
    _add(rows, "some_new_feed")

    row = _get(client).json()["observations"][0]

    assert row["measurement_kind"] == "undeclared"
    assert row["model"] is None


def test_the_raw_metadata_is_never_handed_to_the_caller(client, rows):
    """`metadata_json` is free-form text that happens to hold JSON today.

    Publishing it would make every key any ingester ever wrote part of this
    contract.
    """
    _add(rows, "openmeteo_air_quality",
         metadata='{"measurement_kind": "modelled", "secret": "x"}')

    body = _get(client).json()

    assert "secret" not in str(body)
    assert "metadata_json" not in body["observations"][0]


# --- the filter that must not be mixed ---------------------------------------


def test_the_kind_filter_selects_only_that_kind(client, rows):
    _add(rows, "openmeteo_air_quality")
    _add(rows, "rosstat", indicator="life_expectancy", kind="not_applicable",
         unit="years", value=73.4)

    body = _get(client, measurement_kind="modelled").json()

    assert body["count"] == 1
    assert {r["source"] for r in body["observations"]} == {"openmeteo_air_quality"}


def test_without_the_filter_kinds_are_mixed_but_labelled(client, rows):
    """Mixing is allowed; mixing *unmarked* is not.

    Every row carries its own kind, so a caller that asked for everything can
    still tell what it got.
    """
    _add(rows, "openmeteo_air_quality")
    _add(rows, "rosstat", indicator="life_expectancy", kind="not_applicable",
         unit="years", value=73.4)

    body = _get(client).json()

    assert body["count"] == 2
    assert {r["measurement_kind"] for r in body["observations"]} == {
        "modelled", "administrative_snapshot"}


def test_an_unknown_kind_is_refused_not_answered_with_nothing(client, rows):
    """A typo must not be indistinguishable from a true negative.

    `measurement_kind=measurd` returning an empty page is how a filter comes to
    be trusted for excluding something it never excluded.
    """
    _add(rows, "openmeteo_air_quality")

    response = _get(client, measurement_kind="measurd")

    assert response.status_code == 400
    assert "measurd" in response.json()["detail"]


def test_a_declared_kind_with_no_rows_is_an_empty_page_not_an_error(client, rows):
    """`measured` is declared -- openaq -- and has written nothing (#117)."""
    _add(rows, "openmeteo_air_quality")

    body = _get(client, measurement_kind="measured").json()

    assert body["count"] == 0
    assert body["filters"]["measurement_kind"] == "measured"


# --- the two times stay apart ------------------------------------------------


def test_event_time_and_ingested_at_are_separate_fields(client, rows):
    """Conflating them is what stamped 12,240 rows as observed on the day they
    were ingested (#121)."""
    _add(rows, "openmeteo_air_quality", hours_ago=48)

    row = _get(client).json()["observations"][0]

    assert row["event_time"] is not None
    assert row["ingested_at"] is not None
    assert row["event_time"] != row["ingested_at"]


def test_the_range_filter_applies_to_when_it_happened(client, rows):
    """Not to when we fetched it. All three rows share `ingested_at`, so a
    filter reading that column would return all of them."""
    _add(rows, "openmeteo_air_quality", hours_ago=1)
    _add(rows, "openmeteo_air_quality", hours_ago=50)
    _add(rows, "openmeteo_air_quality", hours_ago=100)

    since = (NOW - timedelta(hours=60)).isoformat()
    body = _get(client, since=since).json()

    assert body["count"] == 2


def test_a_row_with_no_observation_time_is_outside_every_range(client, rows):
    """`not_applicable` sources have none by construction.

    Stated as a case rather than left to be discovered: a reader filtering by
    time silently loses every rosstat and sber row.
    """
    _add(rows, "rosstat", indicator="life_expectancy", kind="not_applicable",
         unit="years", value=73.4)

    since = (NOW - timedelta(days=3650)).isoformat()

    assert _get(client, since=since).json()["count"] == 0
    assert _get(client).json()["count"] == 1


# --- paging ------------------------------------------------------------------


def test_a_page_says_whether_more_exist(client, rows):
    for n in range(3):
        _add(rows, "openmeteo_air_quality", hours_ago=n + 1)

    first = _get(client, limit=2).json()

    assert first["count"] == 2
    assert first["has_more"] is True
    assert _get(client, limit=2, offset=2).json()["has_more"] is False


def test_the_page_carries_the_filters_it_was_built_from(client, rows):
    """A page of 50 modelled rows is identical in shape to 50 measured ones."""
    _add(rows, "openmeteo_air_quality")

    body = _get(client, measurement_kind="modelled", region="RU-MOW").json()

    assert body["filters"]["measurement_kind"] == "modelled"
    assert body["filters"]["region"] == "RU-MOW"


def test_paging_does_not_repeat_or_skip_a_row(client, rows):
    """Rows sharing an `event_time` need a deterministic tie-break.

    Without one the same row can appear on two pages and another on none --
    the failure looks like a count that does not add up, days later.
    """
    for n in range(5):
        _add(rows, "openmeteo_air_quality", hours_ago=1, region=f"RU-{n:02d}")

    seen = []
    for offset in (0, 2, 4):
        seen += [r["id"] for r in _get(client, limit=2, offset=offset).json()["observations"]]

    assert len(seen) == 5
    assert len(set(seen)) == 5


# --- coordinates, now that they exist ---------------------------------------


def test_coordinates_are_served_as_numbers(client, rows):
    _add(rows, "openmeteo_air_quality", lat=55.7558, lon=37.6173)

    row = _get(client).json()["observations"][0]

    assert row["latitude"] == pytest.approx(55.7558)
    assert row["longitude"] == pytest.approx(37.6173)


def test_a_region_level_figure_carries_no_point(client, rows):
    """rosstat publishes for a region, not for a place in it."""
    _add(rows, "rosstat", indicator="life_expectancy", kind="not_applicable",
         unit="years", value=73.4)

    row = _get(client).json()["observations"][0]

    assert row["latitude"] is None
    assert row["longitude"] is None
