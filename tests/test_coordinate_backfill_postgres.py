"""The coordinate backfill reads the row, and survives what the row contains.

Issue #84, migration c4e2a7b91f38. It recovers `latitude`/`longitude` for rows
whose own `metadata_json` recorded them -- `openmeteo_air_quality` -- and
deliberately leaves the 24,140 `openmeteo` rows alone, because their
coordinates were never in the row at all: they existed only as a constant table
in the source tree. Reconstructing them from that constant would attach
wherever it points now to a reading taken then.

`metadata_json` is a `Text` column, so nothing has ever guaranteed that every
value parses as JSON. One malformed row would abort a single
`UPDATE ... ::jsonb` over the table: a migration written to repair data,
defeated by the data.

The statement is executed here rather than restated, so a change to the
migration that breaks it fails this file.
"""
import importlib.util
import json
import os

import pytest
from sqlalchemy import text

from tests.postgres_scratch import (  # noqa: F401  (scratch_db is a fixture)
    requires_postgres,
    scratch_db,
)

pytestmark = requires_postgres

MOSCOW = (55.7558, 37.6173)

INSERT = text(
    "INSERT INTO environmental_observations "
    "  (region_id, indicator, value, source, source_record_id, temporal_kind, "
    "   event_time, metadata_json, ingested_at, created_at, updated_at, is_valid) "
    "VALUES (:region, 'pm2_5', 1.0, :source, :rid, 'observed', now(), "
    "        :metadata, now(), now(), now(), true)"
)


@pytest.fixture(scope="module")
def backfill_sql():
    """The migration's own statement, imported rather than copied."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "alembic", "versions",
        "c4e2a7b91f38_backfill_observation_coordinates.py",
    )
    spec = importlib.util.spec_from_file_location("_backfill_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # `%%` is how the statement escapes a literal `%` for op.execute; the
    # driver here is handed the SQL directly and would see the doubling.
    return module.BACKFILL.replace("%%", "%")


def _add(engine, rid, metadata, source="openmeteo_air_quality"):
    with engine.begin() as conn:
        conn.execute(INSERT, {
            "region": "RU-MOW", "source": source, "rid": rid,
            "metadata": metadata,
        })


def _run(engine, backfill_sql):
    with engine.begin() as conn:
        conn.execute(text(backfill_sql))


def _coords(engine, rid):
    with engine.begin() as conn:
        return conn.execute(text(
            "SELECT latitude, longitude FROM environmental_observations "
            "WHERE source_record_id = :rid"), {"rid": rid}).first()


def test_a_row_that_recorded_its_point_gets_it_back(scratch_db, backfill_sql):
    engine, _ = scratch_db
    lat, lon = MOSCOW
    _add(engine, "a", json.dumps(
        {"latitude": lat, "longitude": lon, "measurement_kind": "modelled"}))

    _run(engine, backfill_sql)

    assert _coords(engine, "a") == pytest.approx((lat, lon))


def test_a_row_that_recorded_nothing_is_left_alone(scratch_db, backfill_sql):
    """The openmeteo case: 24,140 rows with no metadata at all."""
    engine, _ = scratch_db
    _add(engine, "b", None, source="openmeteo")

    _run(engine, backfill_sql)

    assert _coords(engine, "b") == (None, None)


def test_one_malformed_row_does_not_stop_the_others(scratch_db, backfill_sql):
    """The reason this is a loop with an exception block, not one UPDATE.

    The bad row is inserted first so that a statement which aborts on it never
    reaches the good one -- ordering the fixture so the failure it guards
    against is actually reachable.
    """
    engine, _ = scratch_db
    lat, lon = MOSCOW
    _add(engine, "bad", '{"latitude": 55.0, "longitude":')   # truncated JSON
    _add(engine, "good", json.dumps({"latitude": lat, "longitude": lon}))

    _run(engine, backfill_sql)

    assert _coords(engine, "good") == pytest.approx((lat, lon))
    assert _coords(engine, "bad") == (None, None)


def test_a_non_numeric_coordinate_is_skipped_not_stored(scratch_db, backfill_sql):
    engine, _ = scratch_db
    _add(engine, "c", json.dumps({"latitude": "unknown", "longitude": 37.0}))

    _run(engine, backfill_sql)

    assert _coords(engine, "c") == (None, None)


def test_an_out_of_range_coordinate_is_skipped_not_clamped(scratch_db, backfill_sql):
    """Clamping would move a broken reading onto a real point on the map."""
    engine, _ = scratch_db
    _add(engine, "d", json.dumps({"latitude": 991.0, "longitude": 37.0}))

    _run(engine, backfill_sql)

    assert _coords(engine, "d") == (None, None)


def test_a_json_boolean_is_not_a_coordinate(scratch_db, backfill_sql):
    """The same input the ingester refuses, refused here.

    In Python `bool` is a subclass of `int`, so `float(True)` is 1.0 and a
    naive reader stores a point in the Gulf of Guinea. PostgreSQL will not cast
    the text `true` to double precision, so this end refuses it by accident of
    typing -- which is worth a test, because "it happens to fail" and "it is
    specified to fail" look identical until someone rewrites the cast.
    """
    engine, _ = scratch_db
    _add(engine, "bool", '{"latitude": true, "longitude": false}')

    _run(engine, backfill_sql)

    assert _coords(engine, "bool") == (None, None)


def test_half_a_pair_is_not_a_location(scratch_db, backfill_sql):
    engine, _ = scratch_db
    _add(engine, "e", json.dumps({"latitude": 55.0}))

    _run(engine, backfill_sql)

    assert _coords(engine, "e") == (None, None)


def test_running_it_twice_changes_nothing(scratch_db, backfill_sql):
    """`latitude IS NULL` is the guard, so a re-run must be a no-op.

    A migration that is not idempotent is one interrupted deployment away from
    being unrunnable, which is the shape #160 arrived in.
    """
    engine, _ = scratch_db
    lat, lon = MOSCOW
    _add(engine, "f", json.dumps({"latitude": lat, "longitude": lon}))

    _run(engine, backfill_sql)
    first = _coords(engine, "f")
    _run(engine, backfill_sql)

    assert _coords(engine, "f") == pytest.approx(first)


def test_it_does_not_overwrite_a_coordinate_already_stored(scratch_db, backfill_sql):
    """Rows written by the fixed ingester carry the source's own answer.

    The backfill must not reach them: metadata and column could disagree after
    a correction, and the column is the one the writer chose.
    """
    engine, _ = scratch_db
    _add(engine, "g", json.dumps({"latitude": 10.0, "longitude": 20.0}))
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE environmental_observations "
            "SET latitude = 55.0, longitude = 37.0 WHERE source_record_id = 'g'"))

    _run(engine, backfill_sql)

    assert _coords(engine, "g") == pytest.approx((55.0, 37.0))
