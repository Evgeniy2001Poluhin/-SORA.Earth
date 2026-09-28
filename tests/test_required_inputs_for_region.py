"""Test required_inputs_for_region through the real ingest path.

The region detail page shows exactly the six inputs of the ESG score, selected
the same way the aggregator selects them. This tests that the new function
returns them in the declared order with the expected values, units, and periods.
"""
import asyncio
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import EnvironmentalObservation, RegionESGScore, RegionSignal, IngesterRun
from app.services import esg_aggregator
from app.ingesters import persist
from app.ingesters.runner import run_all_ingesters
from app.ingesters import temporal
import app.database as database
from tests.test_esg_aggregator_reads_the_live_source import _create_for_sqlite


@pytest.fixture
def session_factory(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/ri.db")
    _create_for_sqlite(
        (EnvironmentalObservation, RegionESGScore, RegionSignal, IngesterRun), engine
    )
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    for mod in (database, persist, esg_aggregator):
        monkeypatch.setattr(mod, "SessionLocal", factory)
    return factory


def test_required_inputs_for_region_all_present(session_factory):
    """RU-MOW gets six inputs in declared order, values match _latest_by_region.

    Run the real ingesters (sber_veb_baseline + rosstat), then query one region's
    inputs. All six present, none missing; values, units, periods match what the
    aggregator itself uses.
    """
    asyncio.run(run_all_ingesters())

    db = session_factory()
    inputs = esg_aggregator.required_inputs_for_region(db, "RU-MOW")
    db.close()

    # Six inputs in declared order
    assert len(inputs) == 6
    expected_pairs = [
        ("sber_veb_baseline", "esg_index_baseline"),
        ("rosstat", "unemployment_rate"),
        ("rosstat", "avg_income_rub"),
        ("rosstat", "life_expectancy"),
        ("rosstat", "budget_transparency"),
        ("rosstat", "digital_gov_index"),
    ]
    for i, (src, ind) in enumerate(expected_pairs):
        assert inputs[i]["source"] == src
        assert inputs[i]["indicator"] == ind
        assert inputs[i]["missing"] is False

    # Values match _latest_by_region
    db = session_factory()
    metrics_by_region, _, _, _ = esg_aggregator._latest_by_region(db)
    db.close()
    mow_metrics = metrics_by_region.get("RU-MOW") or {}

    for inp in inputs:
        key = f"{inp['source']}:{inp['indicator']}"
        expected_value = esg_aggregator._get(mow_metrics, key)
        assert inp["value"] == expected_value, \
            f"{key}: expected {expected_value}, got {inp['value']}"

    # Rosstat inputs have temporal_kind "period" and 2024 period
    for inp in inputs:
        if inp["source"] == "rosstat":
            assert inp["temporal_kind"] == temporal.PERIOD
            assert inp["period_start"] is not None
            assert inp["period_end"] is not None
            start_year = inp["period_start"].year
            end_year = inp["period_end"].year
            assert start_year == 2024
            assert end_year == 2024

    # Sber input is not_applicable
    sber = next(i for i in inputs if i["source"] == "sber_veb_baseline")
    assert sber["temporal_kind"] == temporal.NOT_APPLICABLE

    # Units are what the rows carry
    db = session_factory()
    for inp in inputs:
        if inp["missing"]:
            continue
        row = db.query(EnvironmentalObservation).filter_by(
            region_id="RU-MOW",
            source=inp["source"],
            indicator=inp["indicator"],
            is_valid=True,
        ).filter(EnvironmentalObservation.value.isnot(None)).first()
        assert row is not None, f"{inp['source']}:{inp['indicator']} not found"
        assert inp["unit"] == row.unit, \
            f"{inp['source']}:{inp['indicator']}: expected unit {row.unit}, got {inp['unit']}"
    db.close()

    # Actual units reported (for the report)
    units_seen = {f"{i['source']}:{i['indicator']}": i['unit'] for i in inputs if not i['missing']}
    print(f"Units seen: {units_seen}")


def test_one_region_missing_one_input(session_factory):
    """Control region RU-SPE with one deleted input shows that input missing."""
    asyncio.run(run_all_ingesters())

    # Delete RU-SPE's unemployment_rate rows
    db = session_factory()
    deleted = db.query(EnvironmentalObservation).filter_by(
        region_id="RU-SPE",
        source="rosstat",
        indicator="unemployment_rate",
    ).delete()
    assert deleted > 0, "Expected to delete unemployment_rate rows for RU-SPE"
    db.commit()
    db.close()

    # Query inputs
    db = session_factory()
    inputs = esg_aggregator.required_inputs_for_region(db, "RU-SPE")
    db.close()

    # Six entries, one missing
    assert len(inputs) == 6
    missing = [i for i in inputs if i["missing"]]
    present = [i for i in inputs if not i["missing"]]
    assert len(missing) == 1
    assert len(present) == 5

    # The missing one is unemployment_rate
    assert missing[0]["source"] == "rosstat"
    assert missing[0]["indicator"] == "unemployment_rate"
    assert missing[0]["value"] is None

    # The other five are present
    expected_present = [
        ("sber_veb_baseline", "esg_index_baseline"),
        ("rosstat", "avg_income_rub"),
        ("rosstat", "life_expectancy"),
        ("rosstat", "budget_transparency"),
        ("rosstat", "digital_gov_index"),
    ]
    for i, (src, ind) in enumerate(expected_present):
        p = next(x for x in present if x["source"] == src and x["indicator"] == ind)
        assert p["value"] is not None
        assert p["missing"] is False


def test_temporal_kind_filter_blocks_wrong_kind(session_factory):
    """Mutation M2: newer row with wrong temporal_kind must NOT outrank the correct one.

    After the real ingest, insert for RU-MOW and rosstat:unemployment_rate a NEWER
    row with temporal_kind=LEGACY (not the expected PERIOD) and a different value.
    Assert both required_inputs_for_region and _latest_by_region still return the
    original PERIOD value and kind, proving the filter in _ranked_required_observations
    excludes the wrong-kind row.
    """
    asyncio.run(run_all_ingesters())

    db = session_factory()
    # Read original unemployment_rate for RU-MOW
    original_row = db.query(EnvironmentalObservation).filter_by(
        region_id="RU-MOW",
        source="rosstat",
        indicator="unemployment_rate",
        is_valid=True,
    ).filter(EnvironmentalObservation.value.isnot(None)).first()
    assert original_row is not None, "Original unemployment_rate not found"
    assert original_row.temporal_kind == temporal.PERIOD
    original_value = original_row.value
    original_kind = original_row.temporal_kind

    # Insert a NEWER row with wrong temporal_kind and different value
    # Make it newer by all ordering keys: later event_time/ingested_at, later id
    wrong_kind_value = original_value + 999.0  # distinctly different
    wrong_kind = temporal.LEGACY  # not the expected PERIOD
    newer_row = EnvironmentalObservation(
        region_id="RU-MOW",
        source="rosstat",
        indicator="unemployment_rate",
        value=wrong_kind_value,
        unit=original_row.unit,
        is_valid=True,
        ingested_at=datetime.now(timezone.utc) + timedelta(hours=1),
        temporal_kind=wrong_kind,
        event_time=datetime.now(timezone.utc) + timedelta(hours=1),  # LEGACY requires event_time
        period_start=None,  # wrong kind has no period
        period_end=None,
        source_revision=original_row.source_revision,
    )
    db.add(newer_row)
    db.commit()
    db.close()

    # required_inputs_for_region must still return the PERIOD row
    db = session_factory()
    inputs = esg_aggregator.required_inputs_for_region(db, "RU-MOW")
    db.close()
    unemp = next(i for i in inputs if i["indicator"] == "unemployment_rate")
    assert unemp["value"] == original_value, \
        f"Expected original value {original_value}, got {unemp['value']} (wrong-kind row leaked through)"
    assert unemp["temporal_kind"] == original_kind, \
        f"Expected original kind {original_kind}, got {unemp['temporal_kind']}"

    # _latest_by_region must also exclude the wrong-kind row
    db = session_factory()
    metrics_by_region, _, _, _ = esg_aggregator._latest_by_region(db)
    db.close()
    mow_metrics = metrics_by_region.get("RU-MOW") or {}
    fetched_value = esg_aggregator._get(mow_metrics, "rosstat:unemployment_rate")
    assert fetched_value == original_value, \
        f"_latest_by_region: expected {original_value}, got {fetched_value}"
