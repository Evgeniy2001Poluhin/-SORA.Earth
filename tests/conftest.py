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


# --- artifact isolation (issue #20) -----------------------------------------
# Redirect the artifact roots onto a throwaway copy BEFORE importing app
# modules, which resolve them once at import time. Without this the suite
# rewrites models/meta.json and appends rows to data/projects.csv — the tracked
# training set — on every run. It has to be a copy rather than an empty
# directory: the same two roots hold the projects CSV and the pickled models
# the endpoints read back.
import atexit
import os
import shutil
import subprocess
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ARTIFACT_TMP = tempfile.mkdtemp(prefix="sora-test-artifacts-")
atexit.register(shutil.rmtree, _ARTIFACT_TMP, True)

for _dirname, _envvar in (("data", "SORA_DATA_DIR"), ("models", "SORA_MODELS_DIR")):
    if os.environ.get(_envvar):
        continue  # honour an explicit override, e.g. from CI
    _scratch = os.path.join(_ARTIFACT_TMP, _dirname)
    shutil.copytree(os.path.join(_REPO_ROOT, _dirname), _scratch)
    os.environ[_envvar] = _scratch


def _dirty_artifacts():
    """Tracked paths under data/ and models/ that differ from HEAD."""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--", "data", "models"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return set()  # no git available; nothing to guard
    if proc.returncode != 0:
        return set()
    # Untracked entries start with "??" and are somebody else's business.
    return {ln[3:] for ln in proc.stdout.splitlines() if ln and not ln.startswith("??")}


_ARTIFACTS_DIRTY_AT_START = _dirty_artifacts()


def pytest_sessionfinish(session, exitstatus):
    """Fail the run if it left tracked artifacts modified.

    Compared against a snapshot taken at start-up so a developer's own
    uncommitted edits under data/ or models/ do not trip this.
    """
    newly_dirty = _dirty_artifacts() - _ARTIFACTS_DIRTY_AT_START
    if not newly_dirty:
        return
    session.exitstatus = 1
    print(
        "\nERROR: this run modified tracked artifacts:\n  "
        + "\n  ".join(sorted(newly_dirty))
        + "\nGenerated files belong under SORA_DATA_DIR / SORA_MODELS_DIR."
    )


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
