"""The aggregator must measure delivery, not the first write.

After #121 a re-run of an unchanged snapshot (rosstat, sber_veb_baseline) is
inserted=0, updated=N by design. The upsert refreshes updated_at but never
ingested_at. Together: 48h after the first write every pair reads as silent
forever, measured on one production run, 2026-09-26 16:05 UTC; by the mechanism,
every run since about 2026-09-04. "Ingestion silent for 571.7h, limit 48h"; all
85 regions refused and marked stale.

The fix: _latest_by_region selects coalesce(updated_at, ingested_at) as the
per-entry timestamp, so a repeat counts as a delivery.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import (
    EnvironmentalObservation, RegionESGScore, RegionSignal, IngesterRun
)
from app.services import esg_aggregator
from app.ingesters import persist
from app.ingesters.runner import run_all_ingesters
from app.ingesters.sber_veb_baseline import SberVebBaselineIngester
from app.ingesters.rosstat import RosstatIngester
import app.database as database
from tests.test_esg_aggregator_reads_the_live_source import _create_for_sqlite


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def session_factory(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/p.db")
    _create_for_sqlite(
        (EnvironmentalObservation, RegionESGScore, RegionSignal, IngesterRun), engine
    )
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    # Three references to SessionLocal; all three are needed so the real
    # runner's writes and the aggregator's reads use the same database.
    for mod in (database, persist, esg_aggregator):
        monkeypatch.setattr(mod, "SessionLocal", factory)
    return factory


def test_a_redelivered_snapshot_is_not_silent(session_factory):
    """The production scenario: real run_all_ingesters() twice with backdating.

    First run: 85 + 425 rows inserted, aggregation success, 85 regions computed.
    Backdate all rows 72h. Second run: persist inserted=0, updated=85+425.
    Aggregation must be success, 85 computed, 0 stale pairs, no stale marks.

    Red on origin/main: test_a_redelivered_snapshot_is_not_silent fails at the
    status assertion (degraded, "ingestion silent for 72.0h", 510 stale pairs);
    test_one_pair_not_redelivered_refuses_only_its_region fails at
    required_pairs_stale (510 == 1); the other three pass.
    """
    # First run: ingestion + aggregation
    stats1 = asyncio.run(run_all_ingesters())
    assert stats1["aggregation"]["status"] == "success"
    assert stats1["aggregation"]["regions_computed"] == 85

    # Assert preconditions: rows were written
    db = session_factory()
    initial_count = db.query(EnvironmentalObservation).count()
    assert initial_count == 510, f"expected 510 rows (85+425), got {initial_count}"
    db.close()

    # Backdate all observation rows 72h (both ingested_at and updated_at)
    db = session_factory()
    for r in db.query(EnvironmentalObservation).all():
        r.updated_at = (r.updated_at or r.ingested_at) - timedelta(hours=72)
        r.ingested_at = r.ingested_at - timedelta(hours=72)
    db.commit()
    db.close()

    # Second run: re-delivery
    stats2 = asyncio.run(run_all_ingesters())

    # Assert it's a re-delivery: inserted=0, updated=received for both sources
    assert stats2["ingesters"]["sber_veb_baseline"]["persist"]["inserted"] == 0
    assert stats2["ingesters"]["sber_veb_baseline"]["persist"]["updated"] == \
           stats2["ingesters"]["sber_veb_baseline"]["persist"]["received"]
    assert stats2["ingesters"]["rosstat"]["persist"]["inserted"] == 0
    assert stats2["ingesters"]["rosstat"]["persist"]["updated"] == \
           stats2["ingesters"]["rosstat"]["persist"]["received"]

    # The fix: aggregation sees the re-delivery as fresh
    assert stats2["aggregation"]["status"] == "success", stats2["aggregation"]
    assert stats2["aggregation"]["regions_computed"] == 85
    assert stats2["aggregation"]["required_pairs_stale"] == 0
    assert stats2["aggregation"]["pipeline_freshness"]["stalled"] is False

    # No region is marked stale
    db = session_factory()
    for row in db.query(RegionESGScore).all():
        assert row.stale_since is None, f"{row.region_code} marked stale"
    db.close()


def test_without_redelivery_the_pipeline_is_still_silent(session_factory):
    """Control: backdating without a second run is still degraded.

    First run + backdating, no second run; recalc_all_regions() must degrade
    (0 computed, 510 stale, stalled). Green on both main and the fix. It guards
    against the opposite mistake to the one fixed here: a change that silences
    the alarm. Without a re-delivery the run must still be degraded.
    """
    asyncio.run(run_all_ingesters())

    # Backdate all rows 72h
    db = session_factory()
    for r in db.query(EnvironmentalObservation).all():
        r.updated_at = (r.updated_at or r.ingested_at) - timedelta(hours=72)
        r.ingested_at = r.ingested_at - timedelta(hours=72)
    db.commit()
    db.close()

    # No second run -- aggregation sees silence
    result = esg_aggregator.recalc_all_regions()

    assert result["status"] == "degraded", result
    assert result["regions_computed"] == 0
    assert result["required_pairs_stale"] == 510
    assert result["pipeline_freshness"]["stalled"] is True


def test_one_pair_not_redelivered_refuses_only_its_region(session_factory):
    """One pair missing from the re-delivery: only that region is refused.

    First run, backdate, re-deliver every signal EXCEPT one rosstat pair.
    Aggregation must be degraded, required_pairs_stale=1, regions_computed=84,
    and the refused region is the one whose pair was dropped.

    Red on main.
    """
    # First run
    asyncio.run(run_all_ingesters())

    # Backdate
    db = session_factory()
    for r in db.query(EnvironmentalObservation).all():
        r.updated_at = (r.updated_at or r.ingested_at) - timedelta(hours=72)
        r.ingested_at = r.ingested_at - timedelta(hours=72)
    db.commit()
    db.close()

    # Re-deliver all signals except one rosstat signal of one region
    sber_signals = asyncio.run(SberVebBaselineIngester().fetch_with_retry())
    rosstat_signals = asyncio.run(RosstatIngester().fetch_with_retry())

    # Drop exactly one signal from one region (pick RU-AD, unemployment_rate)
    dropped_region = "RU-AD"
    dropped_metric = "unemployment_rate"
    original_count = len(rosstat_signals)
    rosstat_signals = [
        s for s in rosstat_signals
        if not (s.region_code == dropped_region and s.metric == dropped_metric)
    ]
    assert len(rosstat_signals) == original_count - 1, "exactly one signal dropped"

    # Re-deliver
    persist.persist_environmental_observations(sber_signals, "sber_veb_baseline")
    persist.persist_environmental_observations(rosstat_signals, "rosstat")

    # Aggregation
    result = esg_aggregator.recalc_all_regions()

    assert result["status"] == "degraded", result
    assert result["required_pairs_stale"] == 1
    assert result["regions_computed"] == 84

    # The refused region is the one we dropped
    db = session_factory()
    refused_row = db.query(RegionESGScore).filter_by(region_code=dropped_region).first()
    assert refused_row is not None, "row existed from first run"
    assert refused_row.stale_since is not None, f"{dropped_region} not marked stale"

    # Other regions are not marked
    for row in db.query(RegionESGScore).filter(RegionESGScore.region_code != dropped_region):
        assert row.stale_since is None, f"{row.region_code} incorrectly marked stale"
    db.close()


def test_a_row_never_updated_is_aged_by_its_first_write(session_factory):
    """The COALESCE fallback: updated_at=None, ingested_at is used.

    Rows with explicit ingested_at and no updated_at. (a) 100h old → degraded;
    (b) 1h old → success. Green on both.
    """
    from tests.test_esg_aggregator_reads_the_live_source import FULL_SET

    # (a) 100h old ingested_at, no updated_at
    db = session_factory()
    old_time = _now() - timedelta(hours=100)
    for region in sorted(esg_aggregator.DECLARED_REGIONS):
        for source, indicator, value in FULL_SET:
            obs = EnvironmentalObservation(
                region_id=region,
                indicator=indicator,
                value=value,
                source=source,
                temporal_kind="not_applicable" if source == "sber_veb_baseline" else "period",
                source_revision="rev:v1:fixture",
                period_start=datetime(2024, 1, 1, tzinfo=timezone.utc) if source == "rosstat" else None,
                period_end=datetime(2024, 12, 31, tzinfo=timezone.utc) if source == "rosstat" else None,
                ingested_at=old_time,
                updated_at=None,  # Explicit None
                is_valid=True,
            )
            db.add(obs)
    db.commit()

    # Assert precondition: updated_at is None on those rows
    for row in db.query(EnvironmentalObservation).all():
        assert row.updated_at is None, "setup must leave updated_at=None"
    db.close()

    result_old = esg_aggregator.recalc_all_regions(max_ingest_age_hours=48)
    assert result_old["status"] == "degraded", result_old
    assert result_old["pipeline_freshness"]["stalled"] is True
    assert result_old["regions_computed"] == 0

    # (b) Fresh ingested_at, no updated_at
    db = session_factory()
    db.query(EnvironmentalObservation).delete()
    fresh_time = _now() - timedelta(hours=1)
    for region in sorted(esg_aggregator.DECLARED_REGIONS):
        for source, indicator, value in FULL_SET:
            obs = EnvironmentalObservation(
                region_id=region,
                indicator=indicator,
                value=value,
                source=source,
                temporal_kind="not_applicable" if source == "sber_veb_baseline" else "period",
                source_revision="rev:v1:fixture",
                period_start=datetime(2024, 1, 1, tzinfo=timezone.utc) if source == "rosstat" else None,
                period_end=datetime(2024, 12, 31, tzinfo=timezone.utc) if source == "rosstat" else None,
                ingested_at=fresh_time,
                updated_at=None,
                is_valid=True,
            )
            db.add(obs)
    db.commit()

    for row in db.query(EnvironmentalObservation).all():
        assert row.updated_at is None
    db.close()

    result_fresh = esg_aggregator.recalc_all_regions(max_ingest_age_hours=48)
    assert result_fresh["status"] == "success", result_fresh
    assert result_fresh["regions_computed"] == 85


def test_redelivery_moves_updated_at_and_not_ingested_at(session_factory):
    """Pin the SQLite writer's update path.

    After first run + backdating + second run, a row's ingested_at still equals
    its backdated value and its updated_at is later than its backdated value by
    at least 71h. Green on both; dropping updated_at from the upsert's update
    path, or adding ingested_at to it, fails this test.
    """
    asyncio.run(run_all_ingesters())

    db = session_factory()
    for r in db.query(EnvironmentalObservation).all():
        r.updated_at = (r.updated_at or r.ingested_at) - timedelta(hours=72)
        r.ingested_at = r.ingested_at - timedelta(hours=72)
    db.commit()

    # Capture backdated values
    backdated_values = {}
    for r in db.query(EnvironmentalObservation).all():
        backdated_values[r.id] = {
            "ingested_at": r.ingested_at,
            "updated_at": r.updated_at,
        }
    db.close()

    # Second run
    asyncio.run(run_all_ingesters())

    # Check the timestamps
    db = session_factory()
    for r in db.query(EnvironmentalObservation).all():
        old_vals = backdated_values[r.id]

        # ingested_at must be unchanged
        assert r.ingested_at == old_vals["ingested_at"], (
            f"row {r.id}: ingested_at changed from {old_vals['ingested_at']} to "
            f"{r.ingested_at}; a repeat must not move it"
        )

        # updated_at must have advanced by at least 71h
        delta = (r.updated_at - old_vals["updated_at"]).total_seconds() / 3600.0
        assert delta >= 71, (
            f"row {r.id}: updated_at advanced by {delta:.1f}h, expected >= 71h; "
            f"a repeat must move it"
        )
    db.close()
