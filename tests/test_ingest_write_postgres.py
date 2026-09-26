"""The ingester write path, on the database that enforces column widths.

#121 shipped with every test green and could not write a single row on
production:

    psycopg.errors.StringDataRightTruncation:
    value too long for type character varying(64)

`content_revision()` returns `rev:v1:` plus a 64-character sha256 -- 71
characters -- and `source_revision` was declared `String(64)`. **SQLite ignores
the length in `VARCHAR(n)`**, so the whole suite stored 71 characters in a
64-character column and reported success. Only PostgreSQL refused, and only
after the release.

`tests/test_column_widths_hold_what_is_written.py` compares the produced values
against the declared widths and so runs everywhere. This file does the thing
itself: build the dict the ingester builds, hand it to the upsert the ingester
uses, against real PostgreSQL migrated to head. A width that the model declares
but the migration never applied would pass there and fail here.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.ingesters import temporal
from app.ingesters.base import Signal
from app.ingesters.persist import _signal_to_observation_dict, _upsert_observations
from tests.postgres_scratch import (  # noqa: F401  (scratch_db is a fixture)
    requires_postgres,
    scratch_db,
)


def _session(engine):
    return sessionmaker(bind=engine)()


def _write(engine, signal, source):
    """The ingester's own two steps, nothing reimplemented."""
    db = _session(engine)
    try:
        obs = _signal_to_observation_dict(signal, source)
        counts = _upsert_observations(db, [obs])
        db.commit()
        return obs, counts
    finally:
        db.close()


@requires_postgres
def test_a_not_applicable_row_with_a_content_revision_is_storable(scratch_db):
    """sber_veb_baseline's shape: no time, revision required.

    This is the exact row that raised StringDataRightTruncation.
    """
    engine, _ = scratch_db

    revision = temporal.content_revision("sber_veb_baseline", {"RU-MOW": 89.0})
    assert len(revision) == 71, (
        f"revision is {len(revision)} characters, not the 71 this test is about; "
        f"if the format changed, the column width has to be rechecked"
    )

    obs, (inserted, updated, duplicates) = _write(
        engine,
        Signal(region_code="RU-MOW", source="sber_veb_baseline",
               metric="esg_index_baseline", value=89.0,
               temporal_kind=temporal.NOT_APPLICABLE, source_revision=revision),
        "sber_veb_baseline",
    )

    assert (inserted, updated, duplicates) == (1, 0, 0)

    with engine.begin() as conn:
        stored = conn.execute(text(
            "SELECT source_revision, temporal_kind, event_time, "
            "       period_start, period_end "
            "FROM environmental_observations WHERE source_record_id = :i"
        ), {"i": obs["source_record_id"]}).one()

    # Read back, not just "the insert did not raise". PostgreSQL would truncate
    # silently if the column were CHAR, and a shorter value that still parses is
    # not the revision that was computed.
    assert stored.source_revision == revision, (
        f"stored {len(stored.source_revision or '')} characters of {len(revision)}"
    )
    assert stored.temporal_kind == temporal.NOT_APPLICABLE
    assert stored.event_time is None
    assert stored.period_start is None and stored.period_end is None


@requires_postgres
def test_a_period_row_with_a_content_revision_is_storable(scratch_db):
    """rosstat's shape: a period, no event time, revision required."""
    engine, _ = scratch_db

    revision = temporal.content_revision("rosstat", {"RU-MOW": 4.2})
    obs, (inserted, _u, _d) = _write(
        engine,
        Signal(region_code="RU-MOW", source="rosstat", metric="unemployment_rate",
               value=4.2, temporal_kind=temporal.PERIOD,
               period_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
               period_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
               source_revision=revision),
        "rosstat",
    )

    assert inserted == 1
    with engine.begin() as conn:
        stored = conn.execute(text(
            "SELECT source_revision, temporal_kind, event_time, period_start "
            "FROM environmental_observations WHERE source_record_id = :i"
        ), {"i": obs["source_record_id"]}).one()

    assert stored.source_revision == revision
    assert stored.temporal_kind == temporal.PERIOD
    assert stored.event_time is None
    assert stored.period_start.year == 2024


@requires_postgres
def test_an_observed_row_still_writes(scratch_db):
    """openmeteo's shape, unchanged by any of this.

    Without it, a fix that broke the honest path would still pass the two above.
    """
    engine, _ = scratch_db

    obs, (inserted, _u, _d) = _write(
        engine,
        Signal(region_code="RU-MOW", source="openmeteo", metric="pm2_5",
               value=7.5, observed_at=datetime(2026, 8, 13, 6, tzinfo=timezone.utc)),
        "openmeteo",
    )

    assert inserted == 1
    with engine.begin() as conn:
        stored = conn.execute(text(
            "SELECT source_record_id, temporal_kind, source_revision "
            "FROM environmental_observations WHERE source_record_id = :i"
        ), {"i": obs["source_record_id"]}).one()

    assert stored.temporal_kind == temporal.OBSERVED
    assert stored.source_revision is None
    assert stored.source_record_id == "RU-MOW_pm2_5_2026-08-13T06:00:00+00:00", (
        "the observed identity is the one already stored on production; "
        "changing it would make every existing row a stranger to dedup"
    )


@requires_postgres
def test_rerunning_the_same_revision_adds_nothing(scratch_db):
    """Dedup on the real unique index, not on the ORM's opinion of it.

    The point of #121: an unchanged literal snapshot must stop producing a new
    row on every run.
    """
    engine, _ = scratch_db

    revision = temporal.content_revision("sber_veb_baseline", {"RU-MOW": 89.0})
    signal = Signal(region_code="RU-MOW", source="sber_veb_baseline",
                    metric="esg_index_baseline", value=89.0,
                    temporal_kind=temporal.NOT_APPLICABLE,
                    source_revision=revision)

    _write(engine, signal, "sber_veb_baseline")
    _obs, (inserted, updated, duplicates) = _write(engine, signal, "sber_veb_baseline")

    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT count(*) FROM environmental_observations "
            "WHERE source = 'sber_veb_baseline'")).scalar()

    assert rows == 1, f"{rows} rows after two identical runs"
    assert inserted == 0, "the second run reported an insert it did not make"
    assert updated + duplicates == 1


@requires_postgres
def test_a_new_revision_is_a_new_row(scratch_db):
    """Otherwise dedup would be indistinguishable from ignoring the source.

    A restated snapshot with different numbers has to land; if it did not, the
    test above would be satisfied by a write path that drops everything.
    """
    engine, _ = scratch_db

    for payload, value in [({"RU-MOW": 89.0}, 89.0), ({"RU-MOW": 91.0}, 91.0)]:
        _write(
            engine,
            Signal(region_code="RU-MOW", source="sber_veb_baseline",
                   metric="esg_index_baseline", value=value,
                   temporal_kind=temporal.NOT_APPLICABLE,
                   source_revision=temporal.content_revision(
                       "sber_veb_baseline", payload)),
            "sber_veb_baseline",
        )

    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT count(*) FROM environmental_observations "
            "WHERE source = 'sber_veb_baseline'")).scalar()

    assert rows == 2, f"{rows} rows for two distinct revisions"


@requires_postgres
def test_the_migrated_column_is_at_least_as_wide_as_the_model_says(scratch_db):
    """The model and the database can disagree.

    A width changed in `app/database.py` without a migration passes every
    in-process check and fails on the first production write -- which is the
    failure this whole file exists for, in a different disguise.
    """
    from app.database import EnvironmentalObservation

    engine, _ = scratch_db
    with engine.begin() as conn:
        actual = {
            r[0]: r[1]
            for r in conn.execute(text(
                "SELECT column_name, character_maximum_length "
                "FROM information_schema.columns "
                "WHERE table_name = 'environmental_observations'"))
        }

    for name in ("source_revision", "source_record_id", "source"):
        declared = EnvironmentalObservation.__table__.columns[name].type.length
        assert actual.get(name) == declared, (
            f"{name}: model declares {declared}, migrated database has "
            f"{actual.get(name)}"
        )


@requires_postgres
def test_a_redelivery_moves_updated_at_and_keeps_ingested_at(scratch_db):
    """The PostgreSQL upsert: a repeat moves updated_at and keeps ingested_at.

    The aggregator's liveness reads updated_at, so this pins the cause on the
    production dialect; test_the_aggregator_counts_a_redelivered_snapshot_as_delivered
    checks the consequence. Write one sber_veb_baseline signal, backdate both
    timestamps 72h, write the same signal again (assert 0 inserted / 1 updated),
    assert ingested_at unchanged and updated_at advanced by at least 71h.
    """
    engine, _ = scratch_db
    revision = temporal.content_revision("sber_veb_baseline", {"RU-MOW": 89.0})
    signal = Signal(
        region_code="RU-MOW", source="sber_veb_baseline",
        metric="esg_index_baseline", value=89.0,
        temporal_kind=temporal.NOT_APPLICABLE,
        source_revision=revision
    )

    # First write
    obs, (inserted, _u, _d) = _write(engine, signal, "sber_veb_baseline")
    assert inserted == 1

    # Backdate both timestamps 72h
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE environmental_observations "
            "SET ingested_at = ingested_at - interval '72 hours', "
            "    updated_at = updated_at - interval '72 hours' "
            "WHERE source_record_id = :i"
        ), {"i": obs["source_record_id"]})

        # Capture backdated values
        backdated = conn.execute(text(
            "SELECT ingested_at, updated_at FROM environmental_observations "
            "WHERE source_record_id = :i"
        ), {"i": obs["source_record_id"]}).one()

    # Second write
    _obs2, (inserted2, updated2, _d2) = _write(engine, signal, "sber_veb_baseline")
    assert inserted2 == 0, "second write reported an insert"
    assert updated2 == 1, "second write did not report an update"

    # Check timestamps
    with engine.begin() as conn:
        final = conn.execute(text(
            "SELECT ingested_at, updated_at FROM environmental_observations "
            "WHERE source_record_id = :i"
        ), {"i": obs["source_record_id"]}).one()

    # ingested_at must be unchanged
    assert final.ingested_at == backdated.ingested_at, (
        f"ingested_at changed from {backdated.ingested_at} to {final.ingested_at}; "
        f"a repeat must not move it"
    )

    # updated_at must have advanced by at least 71h
    delta = (final.updated_at - backdated.updated_at).total_seconds() / 3600.0
    assert delta >= 71, (
        f"updated_at advanced by {delta:.1f}h, expected >= 71h; a repeat must move it"
    )


@requires_postgres
def test_the_aggregator_counts_a_redelivered_snapshot_as_delivered(scratch_db, monkeypatch):
    """End-to-end on PG: real ingesters' signals, backdate, re-deliver, aggregate.

    Writes both ingesters' signals via _upsert_observations, backdates, writes
    again, aggregates → 85 computed, 0 stale pairs.
    """
    import asyncio
    from app.ingesters.sber_veb_baseline import SberVebBaselineIngester
    from app.ingesters.rosstat import RosstatIngester
    import app.services.esg_aggregator as agg

    engine, _ = scratch_db

    # Fetch signals
    sber_signals = asyncio.run(SberVebBaselineIngester().fetch_with_retry())
    rosstat_signals = asyncio.run(RosstatIngester().fetch_with_retry())

    # Write via _upsert_observations
    db = _session(engine)
    try:
        sber_obs = [_signal_to_observation_dict(s, "sber_veb_baseline") for s in sber_signals]
        rosstat_obs = [_signal_to_observation_dict(s, "rosstat") for s in rosstat_signals]
        _upsert_observations(db, sber_obs)
        _upsert_observations(db, rosstat_obs)
        db.commit()
    finally:
        db.close()

    # Backdate all rows 72h
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE environmental_observations "
            "SET ingested_at = ingested_at - interval '72 hours', "
            "    updated_at = updated_at - interval '72 hours'"
        ))

    # Re-fetch and rebuild observation dicts for the second write
    sber_signals = asyncio.run(SberVebBaselineIngester().fetch_with_retry())
    rosstat_signals = asyncio.run(RosstatIngester().fetch_with_retry())

    # Re-write the same signals
    db = _session(engine)
    try:
        sber_obs = [_signal_to_observation_dict(s, "sber_veb_baseline") for s in sber_signals]
        rosstat_obs = [_signal_to_observation_dict(s, "rosstat") for s in rosstat_signals]
        inserted, updated, _ = _upsert_observations(db, sber_obs)
        assert inserted == 0 and updated == len(sber_obs), "sber re-delivery"
        inserted, updated, _ = _upsert_observations(db, rosstat_obs)
        assert inserted == 0 and updated == len(rosstat_obs), "rosstat re-delivery"
        db.commit()
    finally:
        db.close()

    # Aggregate
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(agg, "SessionLocal", factory)
    result = agg.recalc_all_regions()

    assert result["status"] == "success", result
    assert result["regions_computed"] == 85
    assert result["required_pairs_stale"] == 0
