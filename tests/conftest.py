"""Global pytest configuration for SORA.Earth test suite.

IMPORTANT: This conftest is shared by ALL tests. Imports of app.main must be
LAZY (inside fixtures, not module-level) to avoid loading ML models for tests
that don't need FastAPI (e.g., persistence unit tests).
"""
import os

# Prevent APScheduler from starting in tests
os.environ.setdefault("RUN_SCHEDULER", "false")

import pytest
from unittest.mock import MagicMock, patch

# Create in-memory mock cache BEFORE any imports
mock_cache = {}
mock_lists = {}  # For Redis list operations (rpush, lrange)


def mock_get(key):
    return mock_cache.get(key)


def mock_set(key, value, ttl=None):
    mock_cache[key] = value
    return True


def mock_delete(key):
    mock_cache.pop(key, None)
    mock_lists.pop(key, None)
    return True


def mock_ping():
    return True


def mock_rpush(key, *values):
    """Mock Redis rpush for drift detection."""
    if key not in mock_lists:
        mock_lists[key] = []
    mock_lists[key].extend(values)
    return len(mock_lists[key])


def mock_lrange(key, start, stop):
    """Mock Redis lrange for drift detection."""
    if key not in mock_lists:
        return []
    lst = mock_lists[key]
    if stop == -1:
        return lst[start:]
    return lst[start:stop + 1]


def mock_ltrim(key, start, stop):
    """Mock Redis ltrim for drift detection."""
    if key in mock_lists:
        lst = mock_lists[key]
        if stop == -1:
            mock_lists[key] = lst[start:]
        else:
            mock_lists[key] = lst[start:stop + 1]
    return True


# Create mock Redis client
mock_client = MagicMock()
mock_client.get.side_effect = mock_get
mock_client.set.side_effect = mock_set
mock_client.setex.side_effect = lambda k, ttl, v: mock_set(k, v, ttl)
mock_client.delete.side_effect = mock_delete
mock_client.ping.side_effect = mock_ping
mock_client.rpush.side_effect = mock_rpush
mock_client.lrange.side_effect = mock_lrange
mock_client.ltrim.side_effect = mock_ltrim


# Patch redis.from_url BEFORE importing app modules
redis_patcher = patch('redis.from_url', return_value=mock_client)
redis_patcher.start()


@pytest.fixture(scope="session", autouse=True)
def mock_redis_session():
    """Setup Redis mock for the entire test session."""
    import app.redis_cache as rc

    # Replace Redis client in redis_cache.py
    rc.redis_client = mock_client
    rc.REDIS_AVAILABLE = True

    yield

    # Final cleanup
    redis_patcher.stop()


@pytest.fixture(autouse=True)
def clear_redis_cache():
    """Clear Redis mock cache between each test for isolation."""
    yield
    # Clear after each test to prevent state leakage
    mock_cache.clear()
    mock_lists.clear()


@pytest.fixture(scope="session")
def client():
    """FastAPI TestClient fixture - imports app.main LAZILY.

    This fixture is NOT autouse, so it only loads when explicitly requested
    by API tests. Persistence/unit tests that don't need FastAPI should not
    use this fixture.
    """
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def restore_app_models():
    """Restore ML models after test - autouse but LAZY.

    Only restores models if app.main was already imported by the test.
    If app.main is not loaded, this fixture does nothing (no import).
    """
    import sys
    if 'app.main' not in sys.modules:
        # app.main was never imported, nothing to restore
        yield
        return

    # app.main was already imported, take snapshot
    import app.main as m
    snapshot = {
        'rf_model':          m.rf_model,
        'xgb_model':         m.xgb_model,
        'scaler':            m.scaler,
        'ensemble_model_v2': m.ensemble_model_v2,
        'best_threshold':    m.best_threshold,
    }
    yield
    for attr, val in snapshot.items():
        setattr(m, attr, val)


@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    """Clear FastAPI dependency overrides - autouse but LAZY.

    Only clears overrides if app.main was already imported by the test.
    If app.main is not loaded, this fixture does nothing (no import).
    """
    yield
    import sys
    if 'app.main' not in sys.modules:
        # app.main was never imported, nothing to clear
        return

    from app.main import app
    app.dependency_overrides.clear()


@pytest.fixture(scope="session", autouse=True)
def cleanup_scheduler_on_session_end():
    """Ensure APScheduler is shut down after test session.

    This is a safety net for tests that start the scheduler without cleanup.
    Prevents background jobs from running after pytest exits.
    """
    yield

    # After all tests complete, check if scheduler is still running
    import sys
    if 'app.scheduler' in sys.modules:
        from app.scheduler import scheduler
        if scheduler.running:
            from app.scheduler import shutdown_scheduler
            shutdown_scheduler()
            # Fail the test session - scheduler leak is a critical bug
            raise AssertionError(
                "APScheduler was still running after test session! "
                "A test likely started it without cleanup. "
                "This causes background jobs to execute after pytest exits, "
                "leading to HTTP requests and I/O errors."
            )


@pytest.fixture(autouse=True)
def clear_external_cache():
    try:
        from app import external_data as ed
        ed._live_cache.clear()
        ed._cache_timestamps.clear()
    except Exception:
        pass
    yield
