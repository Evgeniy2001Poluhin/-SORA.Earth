"""Tests for environmental data scheduler jobs.

Tests idempotency, Redis locking, job execution, error handling,
and database logging.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone
import time


@pytest.fixture
def mock_redis_lock():
    """Mock Redis lock for testing."""
    with patch("app.locks.RedisLock") as mock:
        lock_instance = Mock()
        lock_instance.acquire.return_value = True
        lock_instance.release.return_value = None
        mock.return_value = lock_instance
        yield mock


@pytest.fixture
def mock_db_session():
    """Mock database session."""
    with patch("app.database.SessionLocal") as mock:
        session = Mock()
        mock.return_value = session
        yield session


class TestOpenAQIngestion:
    """Tests for scheduled_openaq_ingestion job."""

    @patch("app.ingesters.openaq.OpenAQIngester")
    @patch("app.services.environmental.scheduler_jobs._run_async_job")
    @patch("app.services.environmental.scheduler_jobs._log_job_execution")
    def test_successful_ingestion(
        self,
        mock_log,
        mock_run_async,
        mock_ingester_class,
        mock_redis_lock,
    ):
        """Test successful OpenAQ ingestion with valid data."""
        from app.services.environmental.scheduler_jobs import scheduled_openaq_ingestion
        from app.ingesters.base import Signal

        # Mock signals returned from ingester
        mock_signals = [
            Signal(
                region_code="DEU",
                source="openaq",
                metric="pm25_ugm3",
                value=25.5,
                unit="µg/m³",
                observed_at=datetime.now(timezone.utc),
                metadata={"quality": "excellent"},
            ),
            Signal(
                region_code="DEU",
                source="openaq",
                metric="pm10_ugm3",
                value=42.0,
                unit="µg/m³",
                observed_at=datetime.now(timezone.utc),
                metadata={"quality": "good"},
            ),
        ]
        mock_run_async.return_value = mock_signals

        # Execute job
        result = scheduled_openaq_ingestion()

        # Assertions
        assert result["status"] == "success"
        assert result["signals_count"] == 2
        assert result["valid_count"] == 2
        assert result["rejected_count"] == 0
        assert "duration_sec" in result

        # Verify logging was called
        mock_log.assert_called_once()
        log_call = mock_log.call_args
        assert log_call[1]["job_name"] == "openaq_ingestion"
        assert log_call[1]["status"] == "success"
        assert log_call[1]["records_processed"] == 2

    @patch("app.services.environmental.scheduler_jobs._log_job_execution")
    def test_lock_held_skips_execution(self, mock_log):
        """Test that job is skipped when Redis lock is held."""
        from app.services.environmental.scheduler_jobs import scheduled_openaq_ingestion

        # Mock lock as held
        with patch("app.locks.RedisLock") as mock_lock:
            lock_instance = Mock()
            lock_instance.acquire.return_value = False
            mock_lock.return_value = lock_instance

            result = scheduled_openaq_ingestion()

            assert result["status"] == "skipped"
            assert result["reason"] == "lock_held"
            mock_log.assert_called_once_with(
                "openaq_ingestion",
                status="skipped",
                metadata={"reason": "lock_held"}
            )

    @patch("app.ingesters.openaq.OpenAQIngester")
    @patch("app.services.environmental.scheduler_jobs._run_async_job")
    @patch("app.services.environmental.scheduler_jobs._log_job_execution")
    def test_handles_ingestion_error(
        self,
        mock_log,
        mock_run_async,
        mock_ingester_class,
        mock_redis_lock,
    ):
        """Test error handling during ingestion."""
        from app.services.environmental.scheduler_jobs import scheduled_openaq_ingestion

        # Mock ingester failure
        mock_run_async.side_effect = Exception("API unavailable")

        result = scheduled_openaq_ingestion()

        assert result["status"] == "error"
        assert "API unavailable" in result["error"]

        # Verify error logging
        mock_log.assert_called_once()
        log_call = mock_log.call_args
        assert log_call[1]["status"] == "failed"
        assert "API unavailable" in log_call[1]["error_message"]


class TestOpenMeteoIngestion:
    """Tests for scheduled_openmeteo_ingestion job."""

    @patch("app.ingesters.openmeteo.OpenMeteoIngester")
    @patch("app.services.environmental.scheduler_jobs._run_async_job")
    @patch("app.services.environmental.scheduler_jobs._log_job_execution")
    def test_successful_ingestion(
        self,
        mock_log,
        mock_run_async,
        mock_ingester_class,
        mock_redis_lock,
    ):
        """Test successful Open-Meteo ingestion."""
        from app.services.environmental.scheduler_jobs import scheduled_openmeteo_ingestion
        from app.ingesters.base import Signal

        mock_signals = [
            Signal(
                region_code="RU-MOW",
                source="openmeteo",
                metric="temperature_c",
                value=18.5,
                unit="°C",
                observed_at=datetime.now(timezone.utc),
                metadata={},
            ),
        ]
        mock_run_async.return_value = mock_signals

        result = scheduled_openmeteo_ingestion()

        assert result["status"] == "success"
        assert result["signals_count"] == 1
        assert "duration_sec" in result


class TestSourceHealthCheck:
    """Tests for scheduled_source_health_check job."""

    def test_health_check_with_recent_jobs(self, mock_redis_lock, mock_db_session):
        """Test health check when recent jobs exist."""
        from app.services.environmental.scheduler_jobs import scheduled_source_health_check
        from app.database import EnvironmentalJobLog

        # Mock recent successful jobs
        mock_jobs = [
            Mock(
                job_name="openaq_ingestion",
                status="success",
                executed_at=datetime.now(timezone.utc),
                duration_sec=5.2,
            ),
            Mock(
                job_name="openaq_ingestion",
                status="success",
                executed_at=datetime.now(timezone.utc),
                duration_sec=4.8,
            ),
        ]

        mock_query = Mock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = mock_jobs
        mock_db_session.query.return_value = mock_query

        result = scheduled_source_health_check()

        assert result["status"] == "success"
        assert "health_report" in result
        # Verify health report contains both sources
        health = result["health_report"]
        assert "openaq" in health
        assert "openmeteo" in health

    def test_health_check_with_no_recent_jobs(self, mock_redis_lock, mock_db_session):
        """Test health check when no recent jobs exist."""
        from app.services.environmental.scheduler_jobs import scheduled_source_health_check

        # Mock empty query result
        mock_query = Mock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = []
        mock_db_session.query.return_value = mock_query

        result = scheduled_source_health_check()

        assert result["status"] == "success"
        health = result["health_report"]
        assert health["openaq"]["status"] == "unknown"
        assert health["openaq"]["quality_score"] == 0


class TestDataQualityAggregation:
    """Tests for scheduled_data_quality_aggregation job."""

    @patch("app.services.environmental.scheduler_jobs._log_job_execution")
    def test_quality_aggregation(
        self,
        mock_log,
        mock_redis_lock,
        mock_db_session,
    ):
        """Test data quality metrics aggregation."""
        from app.services.environmental.scheduler_jobs import scheduled_data_quality_aggregation

        # Mock database query results
        mock_db_session.query().filter().scalar.side_effect = [
            100,  # openaq total
            95,   # openaq valid
            50,   # openmeteo total
            48,   # openmeteo valid
        ]

        result = scheduled_data_quality_aggregation()

        assert result["status"] == "success"
        assert "quality_stats" in result

        stats = result["quality_stats"]
        assert stats["openaq"]["total_observations"] == 100
        assert stats["openaq"]["valid_observations"] == 95
        assert stats["openaq"]["quality_percentage"] == 95.0

        assert stats["openmeteo"]["total_observations"] == 50
        assert stats["openmeteo"]["valid_observations"] == 48
        assert stats["openmeteo"]["quality_percentage"] == 96.0


class TestJobIdempotency:
    """Tests for job idempotency via Redis locking."""

    def test_concurrent_job_execution_prevented(self):
        """Test that concurrent executions are prevented by lock."""
        from app.services.environmental.scheduler_jobs import scheduled_openaq_ingestion

        # Mock lock acquisition failure (lock already held)
        with patch("app.locks.RedisLock") as mock_lock:
            lock_instance = Mock()
            lock_instance.acquire.return_value = False
            mock_lock.return_value = lock_instance

            result1 = scheduled_openaq_ingestion()
            result2 = scheduled_openaq_ingestion()

            # Both should be skipped
            assert result1["status"] == "skipped"
            assert result2["status"] == "skipped"
            assert lock_instance.acquire.call_count == 2


class TestRetryDecorator:
    """Tests for retry mechanism with exponential backoff."""

    def test_retry_on_failure(self):
        """Test that functions retry on failure."""
        from app.services.environmental.scheduler_jobs import with_retry

        @with_retry(max_attempts=3, base_delay=0.1)
        def failing_function():
            raise Exception("Temporary failure")

        with pytest.raises(Exception, match="Temporary failure"):
            failing_function()

    def test_retry_succeeds_after_failures(self):
        """Test successful execution after initial failures."""
        from app.services.environmental.scheduler_jobs import with_retry

        call_count = 0

        @with_retry(max_attempts=3, base_delay=0.1)
        def eventually_succeeds():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("Not yet")
            return "success"

        result = eventually_succeeds()
        assert result == "success"
        assert call_count == 2


class TestJobLogging:
    """Tests for job execution logging to database."""

    def test_log_successful_job(self, mock_db_session):
        """Test logging of successful job execution."""
        from app.services.environmental.scheduler_jobs import _log_job_execution

        _log_job_execution(
            job_name="test_job",
            status="success",
            duration_sec=5.5,
            records_processed=100,
            records_rejected=5,
            metadata={"test": "data"}
        )

        # Verify database session was used
        mock_db_session.add.assert_called_once()
        mock_db_session.commit.assert_called_once()

    def test_log_failed_job(self, mock_db_session):
        """Test logging of failed job execution."""
        from app.services.environmental.scheduler_jobs import _log_job_execution

        _log_job_execution(
            job_name="test_job",
            status="failed",
            duration_sec=2.1,
            error_message="Connection timeout",
        )

        mock_db_session.add.assert_called_once()
        mock_db_session.commit.assert_called_once()
