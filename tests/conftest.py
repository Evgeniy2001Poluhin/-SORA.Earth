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


# --- offline / scheduler isolation -------------------------------------------
#
# SORA_OFFLINE=1 is meant to guarantee that a test run touches no external
# service. It used to be a convention rather than an enforced boundary: a test
# that assigned RUN_SCHEDULER directly started the real BackgroundScheduler,
# whose ingestion jobs then reached api.open-meteo.com from inside CI.
#
# These fixtures turn the convention into a boundary.

_ALLOWED_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _offline_mode() -> bool:
    import os
    return os.getenv("SORA_OFFLINE", "0") == "1"


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch):
    """Fail fast on any outbound connection while SORA_OFFLINE=1.

    Loopback stays open so the FastAPI TestClient and local sqlite keep working.
    """
    if not _offline_mode():
        yield
        return

    import socket

    real_create_connection = socket.create_connection
    real_connect = socket.socket.connect

    def _host_of(address):
        if isinstance(address, tuple) and address:
            return str(address[0])
        return str(address)

    def guarded_create_connection(address, *args, **kwargs):
        host = _host_of(address)
        if host not in _ALLOWED_HOSTS:
            raise RuntimeError(
                f"Blocked external connection to {host} while SORA_OFFLINE=1. "
                "Mock the client or guard the call site."
            )
        return real_create_connection(address, *args, **kwargs)

    def guarded_connect(self, address, *args, **kwargs):
        host = _host_of(address)
        if self.family in (socket.AF_INET, socket.AF_INET6) and host not in _ALLOWED_HOSTS:
            raise RuntimeError(
                f"Blocked external connection to {host} while SORA_OFFLINE=1. "
                "Mock the client or guard the call site."
            )
        return real_connect(self, address, *args, **kwargs)

    real_getaddrinfo = socket.getaddrinfo

    def guarded_getaddrinfo(host, port, *args, **kwargs):
        # Every outbound connection resolves first, including the asyncio
        # transports that httpx.AsyncClient uses and that never touch
        # socket.create_connection.
        if str(host) not in _ALLOWED_HOSTS:
            raise RuntimeError(
                f"Blocked external DNS lookup for {host} while SORA_OFFLINE=1. "
                "Mock the client or guard the call site."
            )
        return real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    yield


@pytest.fixture(autouse=True)
def no_scheduler_left_running():
    """A test that starts the scheduler must also stop it.

    A BackgroundScheduler thread outlives the test that created it and keeps
    firing ingestion and retrain jobs for the rest of the session.
    """
    yield
    try:
        from app.scheduler import scheduler
    except Exception:
        return
    if scheduler.running:
        scheduler.remove_all_jobs()
        scheduler.shutdown(wait=False)
        raise AssertionError(
            "Test left the APScheduler running; shut it down in a finally block."
        )
