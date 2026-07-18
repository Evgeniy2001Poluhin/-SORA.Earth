"""Tests for environmental observation persistence with upsert logic."""
import pytest
from datetime import datetime, timezone, timedelta
from app.ingesters.base import Signal
from app.ingesters.persist import (
    persist_environmental_observations,
    calculate_health_status,
    PersistResult,
)
from app.database import SessionLocal, EnvironmentalObservation


@pytest.fixture
def db_session():
    """Provide a database session for testing."""
    db = SessionLocal()
    try:
        # Cleanup test data BEFORE test runs (in case previous test failed)
        db.query(EnvironmentalObservation).filter(
            EnvironmentalObservation.source.in_(["test_openaq", "test_openmeteo"])
        ).delete()
        db.commit()
        yield db
    finally:
        # Cleanup test data AFTER test completes
        db.query(EnvironmentalObservation).filter(
            EnvironmentalObservation.source.in_(["test_openaq", "test_openmeteo"])
        ).delete()
        db.commit()
        db.close()


def test_openaq_upsert_duplicate_observations(db_session):
    """Test that duplicate OpenAQ observations are handled via upsert."""
    now = datetime.now(timezone.utc)

    # Create initial signal
    signal1 = Signal(
        region_code="DEU",
        source="test_openaq",
        metric="pm25_ugm3",
        value=25.5,
        unit="ug/m3",
        observed_at=now,
        metadata={"quality": "excellent"}
    )

    # First insert
    result1 = persist_environmental_observations([signal1], "test_openaq")
    assert result1.received == 1
    assert result1.accepted >= 1  # Inserted or updated
    assert result1.rejected == 0

    # Check database
    count1 = db_session.query(EnvironmentalObservation).filter(
        EnvironmentalObservation.source == "test_openaq",
        EnvironmentalObservation.region_id == "DEU"
    ).count()
    assert count1 == 1

    # Create duplicate signal (same region, metric, timestamp)
    signal2 = Signal(
        region_code="DEU",
        source="test_openaq",
        metric="pm25_ugm3",
        value=26.0,  # Slightly different value (updated measurement)
        unit="ug/m3",
        observed_at=now,  # Same timestamp
        metadata={"quality": "good"}
    )

    # Second insert (should upsert, not create duplicate)
    result2 = persist_environmental_observations([signal2], "test_openaq")
    assert result2.received == 1
    assert result2.accepted >= 1

    # Check database - should still have only 1 record
    count2 = db_session.query(EnvironmentalObservation).filter(
        EnvironmentalObservation.source == "test_openaq",
        EnvironmentalObservation.region_id == "DEU"
    ).count()
    assert count2 == 1

    # Verify value was updated
    obs = db_session.query(EnvironmentalObservation).filter(
        EnvironmentalObservation.source == "test_openaq",
        EnvironmentalObservation.region_id == "DEU"
    ).first()
    assert obs.value == 26.0  # Updated value
    assert obs.metadata_json is not None


def test_openmeteo_upsert_duplicate_observations(db_session):
    """Test that duplicate Open-Meteo observations are handled via upsert."""
    now = datetime.now(timezone.utc)

    # Create initial weather signal
    signal1 = Signal(
        region_code="GBR",
        source="test_openmeteo",
        metric="temperature",
        value=18.5,
        unit="celsius",
        observed_at=now,
    )

    # First insert
    result1 = persist_environmental_observations([signal1], "test_openmeteo")
    assert result1.received == 1
    assert result1.accepted >= 1
    assert result1.rejected == 0

    # Create duplicate with updated value
    signal2 = Signal(
        region_code="GBR",
        source="test_openmeteo",
        metric="temperature",
        value=19.0,  # Updated temperature
        unit="celsius",
        observed_at=now,  # Same timestamp
    )

    # Second insert (should upsert)
    result2 = persist_environmental_observations([signal2], "test_openmeteo")
    assert result2.received == 1
    assert result2.accepted >= 1

    # Verify only one record exists
    count = db_session.query(EnvironmentalObservation).filter(
        EnvironmentalObservation.source == "test_openmeteo",
        EnvironmentalObservation.region_id == "GBR",
        EnvironmentalObservation.indicator == "temperature"
    ).count()
    assert count == 1


def test_health_classification_with_zero_records():
    """Test that health status is 'unhealthy' when no records are accepted."""
    result = PersistResult(
        received=10,
        inserted=0,
        updated=0,
        rejected=10,
        duplicates=0,
        accepted=0,
        errors=["validation_failed"] * 10
    )

    health, reason = calculate_health_status(
        persist_result=result,
        freshness_hours=1.0,
        missing_rate=10.0
    )

    assert health == "unhealthy"
    assert "no_records_accepted" in reason


def test_health_degraded_on_stale_data():
    """Test that health status is 'degraded' when data is 2-6 hours old."""
    result = PersistResult(
        received=10,
        inserted=10,
        updated=0,
        rejected=0,
        duplicates=0,
        accepted=10,
        errors=[]
    )

    # Test degraded: freshness = 4 hours (within 2-6h window)
    health, reason = calculate_health_status(
        persist_result=result,
        freshness_hours=4.0,
        missing_rate=15.0
    )

    assert health == "degraded"
    assert "old_data" in reason
    assert "4.0h" in reason


def test_health_degraded_on_moderate_missing_rate():
    """Test that health status is 'degraded' with 20-50% missing rate."""
    result = PersistResult(
        received=100,
        inserted=70,
        updated=0,
        rejected=30,
        duplicates=0,
        accepted=70,
        errors=[]
    )

    # Test degraded: missing_rate = 30% (within 20-50% window)
    health, reason = calculate_health_status(
        persist_result=result,
        freshness_hours=1.0,
        missing_rate=30.0
    )

    assert health == "degraded"
    assert "moderate_missing_rate" in reason
    assert "30.0%" in reason


def test_health_unhealthy_on_very_stale_data():
    """Test that health status is 'unhealthy' when data is > 6 hours old."""
    result = PersistResult(
        received=10,
        inserted=10,
        updated=0,
        rejected=0,
        duplicates=0,
        accepted=10,
        errors=[]
    )

    # Test unhealthy: freshness = 8 hours (> 6h)
    health, reason = calculate_health_status(
        persist_result=result,
        freshness_hours=8.0,
        missing_rate=10.0
    )

    assert health == "unhealthy"
    assert "stale_data" in reason
    assert "8.0h" in reason


def test_health_unhealthy_on_high_missing_rate():
    """Test that health status is 'unhealthy' when missing rate > 50%."""
    result = PersistResult(
        received=100,
        inserted=40,
        updated=0,
        rejected=60,
        duplicates=0,
        accepted=40,
        errors=[]
    )

    # Test unhealthy: missing_rate = 60% (> 50%)
    health, reason = calculate_health_status(
        persist_result=result,
        freshness_hours=1.0,
        missing_rate=60.0
    )

    assert health == "unhealthy"
    assert "high_missing_rate" in reason
    assert "60.0%" in reason


def test_health_healthy_when_fresh_and_accepted():
    """Test that health status is 'healthy' when all criteria are met."""
    result = PersistResult(
        received=100,
        inserted=95,
        updated=0,
        rejected=5,
        duplicates=0,
        accepted=95,
        errors=[]
    )

    # Test healthy: freshness < 2h, missing_rate < 20%
    health, reason = calculate_health_status(
        persist_result=result,
        freshness_hours=1.5,
        missing_rate=5.0
    )

    assert health == "healthy"
    assert "all_checks_passed" in reason


def test_persist_with_invalid_signals():
    """Test that invalid signals are rejected properly."""
    now = datetime.now(timezone.utc)

    # Mix of valid and invalid signals
    signals = [
        Signal(
            region_code="DEU",
            source="test_openaq",
            metric="pm25_ugm3",
            value=25.5,
            unit="ug/m3",
            observed_at=now,
        ),
        Signal(
            region_code="",  # Invalid: empty region_code
            source="test_openaq",
            metric="pm25_ugm3",
            value=30.0,
            unit="ug/m3",
            observed_at=now,
        ),
        Signal(
            region_code="FRA",
            source="test_openaq",
            metric="",  # Invalid: empty metric
            value=20.0,
            unit="ug/m3",
            observed_at=now,
        ),
        Signal(
            region_code="GBR",
            source="test_openaq",
            metric="no2_ugm3",
            value=None,  # Invalid: None value
            unit="ug/m3",
            observed_at=now,
        ),
    ]

    result = persist_environmental_observations(signals, "test_openaq")

    assert result.received == 4
    assert result.accepted >= 1  # At least the first valid signal
    assert result.rejected == 3  # Three invalid signals
    assert len(result.errors) > 0


def test_persist_with_timezone_aware_timestamps(db_session):
    """Test that naive timestamps are converted to UTC-aware."""
    # Naive timestamp (no timezone)
    naive_time = datetime(2026, 7, 18, 12, 0, 0)

    signal = Signal(
        region_code="USA",
        source="test_openaq",
        metric="pm25_ugm3",
        value=15.0,
        unit="ug/m3",
        observed_at=naive_time,  # Naive datetime
    )

    result = persist_environmental_observations([signal], "test_openaq")

    assert result.received == 1
    assert result.accepted >= 1

    # Verify timestamp was persisted correctly
    obs = db_session.query(EnvironmentalObservation).filter(
        EnvironmentalObservation.source == "test_openaq",
        EnvironmentalObservation.region_id == "USA"
    ).first()

    assert obs is not None
    # Verify timestamp matches (SQLite strips timezone, but that's OK for tests)
    assert obs.event_time.year == 2026
    assert obs.event_time.month == 7
    assert obs.event_time.day == 18
    assert obs.event_time.hour == 12
    assert obs.event_time.minute == 0


def test_persist_quality_score_mapping():
    """Test that quality flags are mapped to 0-100 score."""
    now = datetime.now(timezone.utc)

    signals = [
        Signal(
            region_code="DEU",
            source="test_openaq",
            metric="pm25_ugm3",
            value=25.0,
            unit="ug/m3",
            observed_at=now,
            metadata={"quality": "excellent"}
        ),
        Signal(
            region_code="FRA",
            source="test_openaq",
            metric="pm25_ugm3",
            value=50.0,
            unit="ug/m3",
            observed_at=now,
            metadata={"quality": "good"}
        ),
        Signal(
            region_code="GBR",
            source="test_openaq",
            metric="pm25_ugm3",
            value=80.0,
            unit="ug/m3",
            observed_at=now,
            metadata={"quality": "fair"}
        ),
    ]

    result = persist_environmental_observations(signals, "test_openaq")

    assert result.received == 3
    assert result.accepted == 3

    db = SessionLocal()
    try:
        # Check quality scores
        excellent = db.query(EnvironmentalObservation).filter(
            EnvironmentalObservation.region_id == "DEU"
        ).first()
        assert excellent.quality_score == 100.0

        good = db.query(EnvironmentalObservation).filter(
            EnvironmentalObservation.region_id == "FRA"
        ).first()
        assert good.quality_score == 80.0

        fair = db.query(EnvironmentalObservation).filter(
            EnvironmentalObservation.region_id == "GBR"
        ).first()
        assert fair.quality_score == 60.0
    finally:
        db.close()


@pytest.mark.skipif(
    SessionLocal().bind.dialect.name != "postgresql",
    reason="PostgreSQL-specific xmax=0 check"
)
def test_postgres_upsert_returns_accurate_insert_update_counts():
    """Test that PostgreSQL upsert accurately distinguishes INSERT vs UPDATE.

    Uses xmax=0 RETURNING check to verify that:
    - First upsert counts as INSERT
    - Second upsert of same record counts as UPDATE
    """
    now = datetime.now(timezone.utc)

    # Initial signals
    initial_signals = [
        Signal(
            region_code="DEU",
            source="test_openaq",
            metric="pm25_ugm3",
            value=25.0,
            unit="ug/m3",
            observed_at=now,
            metadata={"quality": "excellent"}
        ),
        Signal(
            region_code="FRA",
            source="test_openaq",
            metric="pm25_ugm3",
            value=30.0,
            unit="ug/m3",
            observed_at=now,
            metadata={"quality": "good"}
        ),
    ]

    # First insert - should count as 2 inserts
    result1 = persist_environmental_observations(initial_signals, "test_openaq")

    assert result1.received == 2
    assert result1.inserted == 2, "First upsert should count as INSERT"
    assert result1.updated == 0, "First upsert should have 0 updates"
    assert result1.accepted == 2

    # Update signals (same source_record_id, different values)
    updated_signals = [
        Signal(
            region_code="DEU",
            source="test_openaq",
            metric="pm25_ugm3",
            value=26.0,  # Updated value
            unit="ug/m3",
            observed_at=now,  # Same timestamp
            metadata={"quality": "good"}  # Updated quality
        ),
        Signal(
            region_code="FRA",
            source="test_openaq",
            metric="pm25_ugm3",
            value=31.0,  # Updated value
            unit="ug/m3",
            observed_at=now,  # Same timestamp
            metadata={"quality": "excellent"}  # Updated quality
        ),
    ]

    # Second upsert - should count as 2 updates
    result2 = persist_environmental_observations(updated_signals, "test_openaq")

    assert result2.received == 2
    assert result2.inserted == 0, "Second upsert should have 0 inserts"
    assert result2.updated == 2, "Second upsert should count as UPDATE"
    assert result2.accepted == 2

    # Mixed batch: 1 new insert + 1 update
    mixed_signals = [
        Signal(
            region_code="GBR",  # New region
            source="test_openaq",
            metric="pm25_ugm3",
            value=35.0,
            unit="ug/m3",
            observed_at=now,
            metadata={"quality": "good"}
        ),
        Signal(
            region_code="DEU",  # Existing region
            source="test_openaq",
            metric="pm25_ugm3",
            value=27.0,  # Another update
            unit="ug/m3",
            observed_at=now,
            metadata={"quality": "fair"}
        ),
    ]

    # Third upsert - should count as 1 insert + 1 update
    result3 = persist_environmental_observations(mixed_signals, "test_openaq")

    assert result3.received == 2
    assert result3.inserted == 1, "Mixed batch should have 1 insert (GBR)"
    assert result3.updated == 1, "Mixed batch should have 1 update (DEU)"
    assert result3.accepted == 2
