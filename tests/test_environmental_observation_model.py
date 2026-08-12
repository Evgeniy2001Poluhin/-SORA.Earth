"""Tests for EnvironmentalObservation SQLAlchemy model.

Validates Phase 1.1 requirements from ROADMAP_ENV_CRISIS_2026.md:
- Schema correctness
- Unique constraints
- UTC-aware timestamps
- Index coverage
"""
import pytest
from datetime import datetime, timezone
from app.database import EnvironmentalObservation


def test_environmental_observation_model_exists():
    """Test that EnvironmentalObservation model is properly defined."""
    assert EnvironmentalObservation is not None
    assert EnvironmentalObservation.__tablename__ == "environmental_observations"


def test_environmental_observation_required_fields():
    """Test that model has all required fields from ROADMAP Phase 1.1."""
    required_fields = [
        'id', 'region_id', 'country_code', 'latitude', 'longitude',
        'indicator', 'value', 'unit', 'source', 'source_record_id',
        'event_time', 'published_at', 'ingested_at', 'source_revision',
        'quality_score', 'is_valid', 'metadata_json', 'created_at', 'updated_at'
    ]

    model_columns = {col.name for col in EnvironmentalObservation.__table__.columns}

    for field in required_fields:
        assert field in model_columns, f"Required field '{field}' missing from model"


def test_environmental_observation_nullable_constraints():
    """The temporal columns, after #121.

    This required `event_time` to be NOT NULL. That is the contract #121
    removes: a column that cannot say "there is no observation time" forces
    every writer to invent one, and persistence duly wrote `datetime.now()`
    for two sources that only ever emit literals.

    Replaced rather than deleted -- the nullability of these columns still
    needs asserting, just with the opposite expectation for `event_time`. The
    full state machine lives in tests/test_observation_temporal_invariants.py.
    """
    from app.database import EnvironmentalObservation

    cols = {c.name: c for c in EnvironmentalObservation.__table__.columns}

    assert cols["event_time"].nullable is True
    assert cols["temporal_kind"].nullable is False
    assert cols["period_start"].nullable is True
    assert cols["period_end"].nullable is True
    assert cols["source_revision"].nullable is True
    assert cols["region_id"].nullable is False
    assert cols["indicator"].nullable is False
    assert cols["source"].nullable is False


def test_environmental_observation_primary_key():
    """Test that id is the primary key."""
    table = EnvironmentalObservation.__table__
    pk_columns = [col.name for col in table.primary_key.columns]

    assert len(pk_columns) == 1, "Should have exactly one primary key column"
    assert pk_columns[0] == 'id', "Primary key should be 'id'"


def test_environmental_observation_indexes():
    """Test that required indexes exist for efficient queries."""
    table = EnvironmentalObservation.__table__
    index_columns = set()

    for index in table.indexes:
        for col in index.columns:
            index_columns.add(col.name)

    # Critical indexes for time-series queries (from ROADMAP)
    required_indexed = ['region_id', 'indicator', 'event_time', 'source', 'ingested_at', 'is_valid']

    for field in required_indexed:
        assert field in index_columns, f"Field '{field}' should be indexed for query performance"


def test_environmental_observation_timezone_aware_fields():
    """Test that temporal fields are timezone-aware (UTC requirement)."""
    table = EnvironmentalObservation.__table__

    temporal_fields = ['event_time', 'published_at', 'ingested_at', 'created_at', 'updated_at']

    for field in temporal_fields:
        col = table.columns[field]
        # Check that column type is DateTime with timezone
        assert hasattr(col.type, 'timezone'), f"Field '{field}' should have timezone attribute"
        # In SQLAlchemy, DateTime(timezone=True) sets this
        assert col.type.timezone is True, f"Field '{field}' should be timezone-aware (UTC)"


def test_environmental_observation_field_lengths():
    """Test that string fields have appropriate length constraints."""
    table = EnvironmentalObservation.__table__

    expected_lengths = {
        'region_id': 32,
        'country_code': 3,
        'indicator': 64,
        'unit': 32,
        'source': 64,
        'source_record_id': 128,
        'source_revision': 64,
    }

    for field, expected_length in expected_lengths.items():
        col = table.columns[field]
        assert hasattr(col.type, 'length'), f"Field '{field}' should have length constraint"
        assert col.type.length == expected_length, (
            f"Field '{field}' should have length {expected_length}, got {col.type.length}"
        )


def test_environmental_observation_instantiation():
    """Test that model can be instantiated with valid data."""
    now = datetime.now(timezone.utc)

    obs = EnvironmentalObservation(
        region_id="DEU",
        country_code="DEU",
        latitude=52.52,
        longitude=13.40,
        indicator="pm25",
        value=42.5,
        unit="ug/m3",
        source="openaq",
        source_record_id="openaq_berlin_12345",
        event_time=now,
        ingested_at=now,
        is_valid=True,
        quality_score=95.0
    )

    assert obs.region_id == "DEU"
    assert obs.indicator == "pm25"
    assert obs.value == 42.5
    assert obs.source == "openaq"
    assert obs.is_valid is True
    assert obs.quality_score == 95.0


def test_environmental_observation_defaults():
    """Test that default values are properly set."""
    table = EnvironmentalObservation.__table__

    # is_valid should default to True
    is_valid_col = table.columns['is_valid']
    assert is_valid_col.server_default is not None, "is_valid should have server default"

    # ingested_at should default to NOW()
    ingested_at_col = table.columns['ingested_at']
    assert ingested_at_col.server_default is not None, "ingested_at should default to NOW()"

    # created_at should default to NOW()
    created_at_col = table.columns['created_at']
    assert created_at_col.server_default is not None, "created_at should default to NOW()"


def test_environmental_observation_quality_score_range():
    """Test that quality_score is documented to be 0-100 scale."""
    # This is a documentation test - actual DB constraint would be added in migration
    obs = EnvironmentalObservation(
        region_id="TEST",
        indicator="test_metric",
        source="test_source",
        event_time=datetime.now(timezone.utc),
        quality_score=95.5  # Valid 0-100 value
    )

    assert obs.quality_score == 95.5
    assert 0 <= obs.quality_score <= 100, "quality_score should be in 0-100 range"


def test_environmental_observation_metadata_json_type():
    """Test that metadata_json is Text type for flexible JSON storage."""
    table = EnvironmentalObservation.__table__
    metadata_col = table.columns['metadata_json']

    # Should be Text type (not JSON/JSONB to maintain sqlite compatibility)
    assert metadata_col.type.__class__.__name__ == 'Text', "metadata_json should be Text type"


def test_environmental_observation_float_precision():
    """Test that Float fields can handle precise measurements."""
    obs = EnvironmentalObservation(
        region_id="TEST",
        indicator="temperature",
        source="test",
        event_time=datetime.now(timezone.utc),
        value=23.456789,  # High precision
        latitude=52.5200066,
        longitude=13.404954,
        quality_score=87.65
    )

    # Float fields should preserve reasonable precision
    assert abs(obs.value - 23.456789) < 0.0001
    assert abs(obs.latitude - 52.5200066) < 0.0000001
    assert abs(obs.longitude - 13.404954) < 0.0000001


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
