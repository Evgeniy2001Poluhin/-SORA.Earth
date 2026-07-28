import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

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
    from app.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def restore_app_models():
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
    yield
    from app.main import app
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def clear_external_cache():
    try:
        from app import external_data as ed
        ed._live_cache.clear()
        ed._cache_timestamps.clear()
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def clear_login_rate_limit():
    """Give every test a fresh login budget.

    Login is rate-limited per source address because Argon2 verification is
    deliberately expensive. Every test shares one address -- TestClient reports
    "testclient" -- so without this the eleventh login in a session starts
    getting 429 and the failure lands on whichever test happens to run then.

    This resets the counter rather than raising the limit: the production bound
    is the thing under test elsewhere, and loosening it here would mean the
    suite never exercises the real one.
    """
    try:
        from app.auth import _login_limiter
        _login_limiter.requests.clear()
    except Exception:
        pass
    yield
