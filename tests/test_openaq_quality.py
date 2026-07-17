"""Tests for OpenAQ data quality pipeline.

Tests comprehensive data quality checks, validation, and outlier detection.
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from app.ingesters.openaq import (
    OpenAQIngester,
    DataQuality,
    PARAMETER_RANGES,
)


class TestDataQualityValidation:
    """Test data quality validation checks."""

    @pytest.fixture
    def ingester(self):
        """Create OpenAQ ingester instance."""
        return OpenAQIngester()

    def test_excellent_quality_measurement(self, ingester):
        """Test that valid fresh data gets EXCELLENT quality."""
        now = datetime.now(timezone.utc)
        obs_time = now - timedelta(minutes=10)  # Fresh data

        measurement = {
            "parameter": {"name": "pm25"},
            "value": 25.0,
            "unit": "µg/m³",
            "locationId": "loc123",
        }

        quality, issues = ingester._validate_measurement(
            "pm25", 25.0, obs_time, now, measurement
        )

        assert quality == DataQuality.EXCELLENT
        assert len(issues) == 0

    def test_out_of_range_invalid(self, ingester):
        """Test that out-of-range values are marked INVALID."""
        now = datetime.now(timezone.utc)
        obs_time = now

        measurement = {
            "parameter": {"name": "pm25"},
            "value": 999.0,  # Out of range (max 500)
            "unit": "µg/m³",
            "locationId": "loc123",
        }

        quality, issues = ingester._validate_measurement(
            "pm25", 999.0, obs_time, now, measurement
        )

        assert quality == DataQuality.INVALID
        assert any("out_of_range" in issue for issue in issues)

    def test_stale_data_poor_quality(self, ingester):
        """Test that very old data gets POOR quality."""
        now = datetime.now(timezone.utc)
        obs_time = now - timedelta(hours=25)  # >24h old

        measurement = {
            "parameter": {"name": "pm25"},
            "value": 25.0,
            "unit": "µg/m³",
            "locationId": "loc123",
        }

        quality, issues = ingester._validate_measurement(
            "pm25", 25.0, obs_time, now, measurement
        )

        assert quality == DataQuality.POOR
        assert any("stale_data" in issue for issue in issues)

    def test_old_data_fair_quality(self, ingester):
        """Test that moderately old data gets FAIR quality."""
        now = datetime.now(timezone.utc)
        obs_time = now - timedelta(hours=4)  # >3h old but <24h

        measurement = {
            "parameter": {"name": "pm25"},
            "value": 25.0,
            "unit": "µg/m³",
            "locationId": "loc123",
        }

        quality, issues = ingester._validate_measurement(
            "pm25", 25.0, obs_time, now, measurement
        )

        assert quality == DataQuality.FAIR
        assert any("old_data" in issue for issue in issues)

    def test_unit_mismatch_detected(self, ingester):
        """Test that unit mismatches are detected but not fatal."""
        now = datetime.now(timezone.utc)
        obs_time = now

        measurement = {
            "parameter": {"name": "pm25"},
            "value": 25.0,
            "unit": "ppm",  # Wrong unit
            "locationId": "loc123",
        }

        quality, issues = ingester._validate_measurement(
            "pm25", 25.0, obs_time, now, measurement
        )

        assert quality == DataQuality.GOOD
        assert any("unit_mismatch" in issue for issue in issues)

    def test_suspicious_zero_detected(self, ingester):
        """Test that zero PM values are flagged as suspicious."""
        now = datetime.now(timezone.utc)
        obs_time = now

        measurement = {
            "parameter": {"name": "pm25"},
            "value": 0.0,  # Suspicious
            "unit": "µg/m³",
            "locationId": "loc123",
        }

        quality, issues = ingester._validate_measurement(
            "pm25", 0.0, obs_time, now, measurement
        )

        assert quality == DataQuality.GOOD
        assert any("suspicious_zero" in issue for issue in issues)

    def test_negative_value_invalid(self, ingester):
        """Test that negative values are marked INVALID."""
        now = datetime.now(timezone.utc)
        obs_time = now

        measurement = {
            "parameter": {"name": "pm25"},
            "value": -5.0,  # Negative
            "unit": "µg/m³",
            "locationId": "loc123",
        }

        quality, issues = ingester._validate_measurement(
            "pm25", -5.0, obs_time, now, measurement
        )

        assert quality == DataQuality.INVALID
        assert any("negative_value" in issue for issue in issues)

    def test_missing_location_id_detected(self, ingester):
        """Test that missing location ID is flagged."""
        now = datetime.now(timezone.utc)
        obs_time = now

        measurement = {
            "parameter": {"name": "pm25"},
            "value": 25.0,
            "unit": "µg/m³",
            # Missing locationId
        }

        quality, issues = ingester._validate_measurement(
            "pm25", 25.0, obs_time, now, measurement
        )

        assert quality == DataQuality.GOOD
        assert any("missing_location_id" in issue for issue in issues)

    def test_all_parameters_have_ranges(self):
        """Test that all parameters have validation ranges defined."""
        expected_params = ["pm25", "pm10", "o3", "no2", "so2", "co"]
        for param in expected_params:
            assert param in PARAMETER_RANGES
            meta = PARAMETER_RANGES[param]
            assert "min" in meta
            assert "max" in meta
            assert "unit" in meta
            assert "metric" in meta


class TestOutlierDetection:
    """Test IQR-based outlier detection."""

    @pytest.fixture
    def ingester(self):
        """Create OpenAQ ingester instance."""
        return OpenAQIngester()

    def test_outlier_detection_iqr(self, ingester):
        """Test that statistical outliers are detected using IQR."""
        from app.ingesters.base import Signal
        now = datetime.now(timezone.utc)

        # Create signals with one clear outlier (need more data points for IQR)
        signals = [
            Signal(
                region_code="USA",
                source="openaq",
                metric="pm25_ugm3",
                value=10.0,
                unit="ug/m3",
                observed_at=now,
                metadata={"quality": "excellent", "quality_issues": []},
            ),
            Signal(
                region_code="GBR",
                source="openaq",
                metric="pm25_ugm3",
                value=12.0,
                unit="ug/m3",
                observed_at=now,
                metadata={"quality": "excellent", "quality_issues": []},
            ),
            Signal(
                region_code="FRA",
                source="openaq",
                metric="pm25_ugm3",
                value=15.0,
                unit="ug/m3",
                observed_at=now,
                metadata={"quality": "excellent", "quality_issues": []},
            ),
            Signal(
                region_code="ESP",
                source="openaq",
                metric="pm25_ugm3",
                value=14.0,
                unit="ug/m3",
                observed_at=now,
                metadata={"quality": "excellent", "quality_issues": []},
            ),
            Signal(
                region_code="ITA",
                source="openaq",
                metric="pm25_ugm3",
                value=13.0,
                unit="ug/m3",
                observed_at=now,
                metadata={"quality": "excellent", "quality_issues": []},
            ),
            Signal(
                region_code="DEU",
                source="openaq",
                metric="pm25_ugm3",
                value=200.0,  # Clear outlier
                unit="ug/m3",
                observed_at=now,
                metadata={"quality": "excellent", "quality_issues": []},
            ),
        ]

        result = ingester._detect_outliers(signals)

        # Find the outlier signal
        outlier = next(s for s in result if s.value == 200.0)
        assert outlier.metadata.get("outlier") is True
        assert "iqr_bounds" in outlier.metadata
        assert outlier.metadata["quality"] == "good"  # Downgraded

    def test_no_outliers_in_normal_data(self, ingester):
        """Test that normal data doesn't get flagged as outliers."""
        from app.ingesters.base import Signal
        now = datetime.now(timezone.utc)

        # Create signals with similar values
        signals = [
            Signal(
                region_code=f"LOC{i}",
                source="openaq",
                metric="pm25_ugm3",
                value=float(10 + i),
                unit="ug/m3",
                observed_at=now,
                metadata={"quality": "excellent", "quality_issues": []},
            )
            for i in range(10)
        ]

        result = ingester._detect_outliers(signals)

        # No signals should be flagged as outliers
        outliers = [s for s in result if s.metadata and s.metadata.get("outlier")]
        assert len(outliers) == 0

    def test_insufficient_data_for_iqr(self, ingester):
        """Test that IQR is not applied with < 4 data points."""
        from app.ingesters.base import Signal
        now = datetime.now(timezone.utc)

        signals = [
            Signal(
                region_code="USA",
                source="openaq",
                metric="pm25_ugm3",
                value=10.0,
                unit="ug/m3",
                observed_at=now,
                metadata={"quality": "excellent"},
            ),
            Signal(
                region_code="GBR",
                source="openaq",
                metric="pm25_ugm3",
                value=200.0,  # Would be outlier with enough data
                unit="ug/m3",
                observed_at=now,
                metadata={"quality": "excellent"},
            ),
        ]

        result = ingester._detect_outliers(signals)

        # No outlier detection with < 4 points
        outliers = [s for s in result if s.metadata and s.metadata.get("outlier")]
        assert len(outliers) == 0


class TestProcessMeasurement:
    """Test measurement processing with quality checks."""

    @pytest.fixture
    def ingester(self):
        """Create OpenAQ ingester instance."""
        return OpenAQIngester()

    def test_process_valid_measurement(self, ingester):
        """Test processing a valid measurement."""
        now = datetime.now(timezone.utc)
        obs_time_str = (now - timedelta(minutes=10)).isoformat() + "Z"

        measurement = {
            "parameter": {"name": "pm25"},
            "value": 25.0,
            "unit": "µg/m³",
            "locationId": "loc123",
            "period": {
                "datetimeLast": {
                    "utc": obs_time_str
                }
            }
        }

        signal = ingester._process_measurement(measurement, "USA", now)

        assert signal is not None
        assert signal.metric == "pm25_ugm3"
        assert signal.value == 25.0
        assert signal.region_code == "USA"
        assert signal.metadata["quality"] == "excellent"
        assert signal.metadata["data_age_hours"] < 1.0

    def test_process_unknown_parameter(self, ingester):
        """Test that unknown parameters are skipped."""
        now = datetime.now(timezone.utc)

        measurement = {
            "parameter": {"name": "unknown_param"},
            "value": 25.0,
            "unit": "µg/m³",
            "locationId": "loc123",
        }

        signal = ingester._process_measurement(measurement, "USA", now)
        assert signal is None

    def test_process_null_value(self, ingester):
        """Test that null values are skipped."""
        now = datetime.now(timezone.utc)

        measurement = {
            "parameter": {"name": "pm25"},
            "value": None,
            "unit": "µg/m³",
            "locationId": "loc123",
        }

        signal = ingester._process_measurement(measurement, "USA", now)
        assert signal is None

    def test_process_invalid_measurement_skipped(self, ingester):
        """Test that invalid measurements are skipped."""
        now = datetime.now(timezone.utc)

        measurement = {
            "parameter": {"name": "pm25"},
            "value": 999.0,  # Out of range
            "unit": "µg/m³",
            "locationId": "loc123",
        }

        signal = ingester._process_measurement(measurement, "USA", now)
        assert signal is None  # Invalid measurements return None


class TestQualityStatistics:
    """Test quality statistics tracking."""

    @pytest.fixture
    def ingester(self):
        """Create OpenAQ ingester instance."""
        return OpenAQIngester()

    def test_quality_stats_initialization(self, ingester):
        """Test that quality stats are initialized correctly."""
        assert ingester.quality_stats["total"] == 0
        assert ingester.quality_stats["excellent"] == 0
        assert ingester.quality_stats["good"] == 0
        assert ingester.quality_stats["invalid"] == 0

    def test_quality_stats_updated_during_processing(self, ingester):
        """Test that quality stats are updated during measurement processing."""
        now = datetime.now(timezone.utc)

        # Process excellent measurement
        measurement1 = {
            "parameter": {"name": "pm25"},
            "value": 25.0,
            "unit": "µg/m³",
            "locationId": "loc123",
        }
        ingester._process_measurement(measurement1, "USA", now)

        # Process invalid measurement
        measurement2 = {
            "parameter": {"name": "pm25"},
            "value": 999.0,  # Out of range
            "unit": "µg/m³",
            "locationId": "loc123",
        }
        ingester._process_measurement(measurement2, "USA", now)

        assert ingester.quality_stats["total"] == 2
        assert ingester.quality_stats["excellent"] == 1
        assert ingester.quality_stats["invalid"] == 1


def test_fetch_with_no_api_key():
    """Test that fetch returns empty list when no API key."""
    import asyncio
    with patch.dict("os.environ", {}, clear=True):
        ingester = OpenAQIngester()
        result = asyncio.run(ingester.fetch())
        assert result == []


def test_ingester_initialization():
    """Test that ingester initializes with correct quality stats."""
    ingester = OpenAQIngester()
    assert ingester.name == "openaq"
    assert ingester.default_ttl_hours == 1
    assert ingester.quality_stats["total"] == 0
    assert "excellent" in ingester.quality_stats
    assert "invalid" in ingester.quality_stats
