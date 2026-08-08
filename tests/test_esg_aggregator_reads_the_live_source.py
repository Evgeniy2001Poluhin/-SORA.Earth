"""The ESG aggregator must read the table that is being written, and say when it isn't.

Until #116 it read `region_signals`, which took its last row on 2026-07-30 when
the ingester runner moved to `persist_environmental_observations`. It kept
recomputing the same 85 scores from frozen data and returning `{"status": "ok"}`
after every run, for eight days, while the map served those scores as current.

Three separate properties here, and they fail independently:

  - the source is the live table, not the dead one
  - a source that is empty or stale is degraded, not a success
  - `regions_written` counts rows the database actually changed

The third exists because of the mechanism the last test pins down: when the
source has not moved, recomputed values are identical, SQLAlchemy emits no
UPDATE, and `onupdate=func.now()` never fires. `updated_at` therefore keeps
meaning "when the value last changed" and can never answer "did anything run".
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import Integer, MetaData, create_engine
from sqlalchemy.orm import sessionmaker

from app.database import (
    EnvironmentalObservation, RegionESGScore, RegionSignal,
)
from app.services import esg_aggregator


def _now():
    return datetime.now(timezone.utc)


def _create_for_sqlite(models, engine):
    """Create each model's table on SQLite, deriving the DDL from the model.

    `RegionESGScore.id` is `BigInteger, Identity(always=True)` -- correct for
    PostgreSQL and impossible on SQLite, which assigns a rowid only to an
    `INTEGER PRIMARY KEY` exactly. Creating straight from the model yields
    `id BIGINT NOT NULL` and every insert fails.

    The tables are copied from the models rather than written out here, so a
    column added to a model appears in the fixture automatically. Only the
    identity is traded for plain autoincrement; the real DDL is covered against
    PostgreSQL by tests/test_migration_region_esg_scores.py, and is not what
    these tests are about.
    """
    meta = MetaData()
    for model in models:
        table = model.__table__.to_metadata(meta)
        pk = table.c[list(model.__table__.primary_key.columns)[0].name]
        pk.identity = None
        pk.type = Integer()
        pk.autoincrement = True
    meta.create_all(bind=engine)


@pytest.fixture
def session_factory(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/agg.db")
    _create_for_sqlite(
        (EnvironmentalObservation, RegionESGScore, RegionSignal), engine
    )
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(esg_aggregator, "SessionLocal", factory)
    return factory


def _observe(session, region, source, indicator, value, event_time=None, valid=True):
    session.add(EnvironmentalObservation(
        region_id=region,
        indicator=indicator,
        value=value,
        source=source,
        event_time=event_time or _now(),
        ingested_at=event_time or _now(),
        is_valid=valid,
    ))


def _full_region(session, region="RU-MOS", event_time=None, base=70.0):
    """Every metric the formula requires, so a score can actually be produced."""
    for source, indicator, value in (
        ("sber_veb_baseline", "esg_index_baseline", base),
        ("rosstat", "unemployment_rate", 5.0),
        ("rosstat", "avg_income_rub", 75000.0),
        ("rosstat", "life_expectancy", 73.0),
        ("rosstat", "budget_transparency", 60.0),
        ("rosstat", "digital_gov_index", 80.0),
    ):
        _observe(session, region, source, indicator, value, event_time)


def test_it_reads_the_live_observation_table(session_factory):
    """The point of #116. Reading region_signals, this finds nothing."""
    s = session_factory()
    _full_region(s)
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions()

    assert result["status"] == "success", result
    assert result["regions_computed"] == 1
    assert result["observations_examined"] == 6

    s = session_factory()
    row = s.query(RegionESGScore).filter_by(region_code="RU-MOS").one()
    assert row.total_score is not None
    assert row.signals_used == 6
    s.close()


def test_the_dead_table_is_not_consulted(session_factory):
    """Rows in region_signals must not produce a score.

    Without this the fix could be "read both", which would keep serving frozen
    values for any region the live table has not reached yet.
    """
    s = session_factory()
    for source, metric, value in (
        ("sber_veb_baseline", "esg_index_baseline", 70.0),
        ("rosstat", "unemployment_rate", 5.0),
    ):
        s.add(RegionSignal(region_code="RU-SPE", source=source, metric=metric,
                           value=value, observed_at=_now(), created_at=_now()))
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions()

    assert result["regions_computed"] == 0
    assert result["status"] == "degraded"

    s = session_factory()
    assert s.query(RegionESGScore).count() == 0
    s.close()


def test_an_empty_source_is_degraded_not_a_success(session_factory):
    """Reported as success it is indistinguishable from a run that had data."""
    result = esg_aggregator.recalc_all_regions()

    assert result["status"] == "degraded"
    assert result["observations_examined"] == 0
    assert "no valid observations" in result["reason"]


def test_a_stale_source_is_degraded_not_a_success(session_factory):
    s = session_factory()
    _full_region(s, event_time=_now() - timedelta(hours=100))
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions(max_age_hours=48)

    assert result["status"] == "degraded", result
    assert "100" in result["reason"] or "old" in result["reason"]
    assert result["newest_age_hours"] >= 99
    # The scores are still computed and written -- degraded is about trust in
    # the inputs, not a refusal to record what they imply.
    assert result["regions_computed"] == 1


def test_fresh_data_is_not_degraded(session_factory):
    """The staleness check must be able to pass, or it asserts nothing."""
    s = session_factory()
    _full_region(s, event_time=_now() - timedelta(hours=1))
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions(max_age_hours=48)

    assert result["status"] == "success", result
    assert "reason" not in result


def test_invalid_observations_are_not_read(session_factory):
    s = session_factory()
    _full_region(s)
    _observe(s, "RU-MOS", "rosstat", "unemployment_rate", 99.0, valid=False)
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions()

    assert result["observations_examined"] == 6


def test_indicators_outside_the_required_set_are_not_read(session_factory):
    """openmeteo covers 21 regions the score table does not score.

    Reading everything would create score rows for country codes (BRA, CAN, ...)
    that carry no rosstat or sber_veb data at all.
    """
    s = session_factory()
    _full_region(s)
    _observe(s, "BRA", "openmeteo", "temperature", 28.0)
    _observe(s, "BRA", "openmeteo_air_quality", "pm2_5", 12.0)
    s.commit()
    s.close()

    result = esg_aggregator.recalc_all_regions()

    assert result["observations_examined"] == 6
    assert result["regions_computed"] == 1

    s = session_factory()
    assert s.query(RegionESGScore).filter_by(region_code="BRA").count() == 0
    s.close()


def test_the_newest_observation_wins(session_factory):
    s = session_factory()
    _full_region(s, event_time=_now() - timedelta(hours=5), base=50.0)
    _observe(s, "RU-MOS", "sber_veb_baseline", "esg_index_baseline", 90.0,
             event_time=_now() - timedelta(hours=1))
    s.commit()
    s.close()

    esg_aggregator.recalc_all_regions()

    s = session_factory()
    row = s.query(RegionESGScore).filter_by(region_code="RU-MOS").one()
    # env_score is the baseline, so it reads back the newer of the two.
    assert row.env_score == 90.0
    s.close()


def test_unchanged_values_emit_no_update_so_updated_at_cannot_show_a_run(session_factory):
    """The mechanism that made the eight-day staleness invisible.

    `RegionESGScore.updated_at` carries `onupdate=func.now()`, so the obvious
    reading is that any recomputation refreshes it. It does not: when every
    value is identical SQLAlchemy issues no UPDATE for the row, and a column
    default that fires on UPDATE cannot fire without one.

    This is pinned as a characterization test rather than fixed, because the
    behaviour is correct -- `updated_at` means "when the value last changed".
    What follows is that the job must report its own freshness, which is what
    `regions_written` and `newest_age_hours` are for.
    """
    s = session_factory()
    _full_region(s)
    s.commit()
    s.close()

    first = esg_aggregator.recalc_all_regions()
    assert first["regions_written"] == 1

    s = session_factory()
    stamp_after_first = s.query(RegionESGScore).one().updated_at
    s.close()

    second = esg_aggregator.recalc_all_regions()

    assert second["regions_computed"] == 1, "it still examined the region"
    assert second["regions_written"] == 0, "but changed nothing"

    s = session_factory()
    assert s.query(RegionESGScore).one().updated_at == stamp_after_first, (
        "no UPDATE was emitted, so onupdate did not fire -- which is why "
        "staleness has to be reported by the job itself"
    )
    s.close()


def test_a_changed_value_is_counted_as_written(session_factory):
    """regions_written must be able to be non-zero on a second run."""
    s = session_factory()
    _full_region(s, base=70.0)
    s.commit()
    s.close()

    esg_aggregator.recalc_all_regions()

    s = session_factory()
    _observe(s, "RU-MOS", "sber_veb_baseline", "esg_index_baseline", 85.0,
             event_time=_now() + timedelta(hours=1))
    s.commit()
    s.close()

    second = esg_aggregator.recalc_all_regions()

    assert second["regions_written"] == 1
    s = session_factory()
    assert s.query(RegionESGScore).one().env_score == 85.0
    s.close()
