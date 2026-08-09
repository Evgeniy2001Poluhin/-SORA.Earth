"""Global pytest configuration for SORA.Earth test suite.

IMPORTANT: This conftest is shared by ALL tests. Imports of app.main must be
LAZY (inside fixtures, not module-level) to avoid loading ML models for tests
that don't need FastAPI (e.g., persistence unit tests).
"""
import os
import shutil
import tempfile

# Prevent APScheduler from starting in tests
os.environ.setdefault("RUN_SCHEDULER", "false")

import pytest
from unittest.mock import MagicMock, patch


def _isolate_test_database():
    """Give the suite its own database file, before anything can open one.

    `app/database.py` reads `DATABASE_URL` and builds the engine at import time,
    and its default is `data/sora.db` inside the repository. That file survives
    between runs, and `create_all(checkfirst=True)` only creates missing
    *tables* -- it never alters an existing one. So a column added to a model
    silently did not reach the tests: the suite kept using a schema built by
    some earlier session, and the failure surfaced as `no such column` from deep
    inside persistence rather than as anything about the schema.

    A session fixture would be too late. This runs at conftest import, above the
    `from app.database import ...` below, which is the first import of it.

    The database is taken only from `TEST_DATABASE_URL`, never from an ambient
    `DATABASE_URL`. An earlier version used `setdefault`, which keeps whatever
    is already exported -- safe against overwriting CI's configuration, and
    unsafe in the direction that matters: a shell holding a production URL
    would hand the suite production, and the suite writes. CI passes the same
    ephemeral PostgreSQL URL as `TEST_DATABASE_URL`, so nothing there is lost.

    A file rather than `:memory:`. In-memory SQLite gives each connection its
    own database, so anything crossing a connection boundary would see an empty
    schema -- trading this class of false result for a new one.

    `mkdtemp` rather than `tmp_path_factory` for the same timing reason, and it
    is per-process, so xdist workers cannot collide.
    """
    from tests._database_url import choose_database_url

    url, directory = choose_database_url(os.environ)
    os.environ["DATABASE_URL"] = url
    return directory


_TEST_DB_DIR = _isolate_test_database()


def pytest_sessionfinish(session, exitstatus):
    """Remove what this process created, and only that."""
    if _TEST_DB_DIR:
        shutil.rmtree(_TEST_DB_DIR, ignore_errors=True)


def _provision_test_schema():
    """Build the schema for the suite's scratch database.

    The application used to do this as a side effect of being imported, which is
    what let four tables exist with no migration behind them (issue #51). It now
    checks the schema and refuses to start without it, so the harness provisions
    it -- explicitly, and here, because pytest imports this file before any test
    module imports the application.

    Only app.database is imported: it defines the models and the engine without
    loading the ML models that app.main pulls in.
    """
    from app.database import Base, engine

    # Refuse anything that is not obviously a throwaway. checkfirst=True skips
    # existing tables but still issues CREATE TABLE for missing ones, so pointing
    # DATABASE_URL at a shared or production database and running pytest would
    # change that schema -- which is the exact behaviour this branch removes from
    # the application. Reintroducing it in the harness would be worse, not better.
    # The guard is on the *action*, not on the name. An allowlist of safe names
    # cannot be written -- the CI databases are called sora_earth and
    # sora_bootstrap, and an earlier version of this refused both.
    #
    # So: if nothing is missing, nothing is created, and there is nothing to
    # guard against. That is the CI case, where `alembic upgrade head` has
    # already run. Only when tables would actually be created does it matter
    # which database this is, and then only SQLite is taken as obviously
    # disposable.
    from sqlalchemy import inspect

    missing = sorted(set(Base.metadata.tables) - set(inspect(engine).get_table_names()))
    if not missing:
        return
    if engine.url.get_backend_name() != "sqlite":
        raise RuntimeError(
            "refusing to create %d table(s) in %r: %s\n"
            "The suite provisions only SQLite for itself. Against a server "
            "database, run `alembic upgrade head` first -- creating tables here "
            "is the behaviour this branch removes from the application."
            % (len(missing), engine.url.database, ", ".join(missing)))

    Base.metadata.create_all(bind=engine, checkfirst=True)


_provision_test_schema()

# Create in-memory mock cache BEFORE any imports
mock_cache = {}
mock_lists = {}  # For Redis list operations (rpush, lrange)
mock_expiry = {}  # key -> seconds requested at write time, for ttl()


def mock_get(key):
    return mock_cache.get(key)


def mock_set(key, value, ttl=None, ex=None, px=None, nx=False, xx=False):
    """Redis SET, accepting the expiry keywords real callers actually pass.

    This took `ttl` only, so `client.set(key, value, ex=seconds)` -- the form
    redis-py documents and the refresh-token store uses -- raised TypeError.
    Callers that wrap the write in `except Exception` then logged a warning and
    carried on, so the value was never stored and nothing said so out loud.

    The expiry is recorded rather than enforced. Nothing here ages entries, so a
    test must not expect a key to vanish; but ttl() can report what was asked
    for, which is what a caller checking "does this expire with the token"
    actually needs to know.
    """
    mock_cache[key] = value
    seconds = ex if ex is not None else (px / 1000 if px is not None else ttl)
    if seconds is not None:
        mock_expiry[key] = int(seconds)
    return True


def mock_delete(key):
    mock_cache.pop(key, None)
    mock_lists.pop(key, None)
    mock_expiry.pop(key, None)
    return True


def mock_ttl(key):
    """Redis TTL, with its actual return values.

    -2 when the key is absent and -1 when it exists without an expiry. A fake
    returning 0 or None for those would read as "expires immediately", which is
    the opposite of what -1 means.
    """
    if key not in mock_cache and key not in mock_lists:
        return -2
    return mock_expiry.get(key, -1)


def mock_scan_iter(match=None, count=None):
    """Redis SCAN_ITER over the keys this fake holds.

    Unconfigured, this returned a MagicMock, and iterating one raises TypeError
    -- which at least fails loudly. `exists` was the dangerous case: it answered
    truthy for every key and nothing looked wrong.
    """
    import fnmatch
    keys = list(mock_cache) + [k for k in mock_lists if k not in mock_cache]
    if match is None:
        yield from keys
        return
    yield from (k for k in keys if fnmatch.fnmatch(k, match))


def mock_ping():
    return True


def mock_exists(*keys):
    """Redis EXISTS, backed by the same store the other operations use.

    Without this the shared fake answered `exists` with an unconfigured
    MagicMock, which is truthy -- so any code asking "is this key present?" was
    told yes for every key it ever asked about. That is what broke the refresh
    token store: every freshly minted token read as revoked, in the full suite
    only, because the fake is session-scoped and a file run alone never sees it.

    The failure mode is worse than a missing method, which would have raised.
    A mock that answers questions it was never taught agrees with whatever the
    caller hoped, and the tests it breaks look like defects in the code.
    """
    return sum(1 for key in keys if key in mock_cache or key in mock_lists)


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
mock_client.exists.side_effect = mock_exists
mock_client.ttl.side_effect = mock_ttl
mock_client.scan_iter.side_effect = mock_scan_iter
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
def reset_rate_limiter():
    """Give every test its own request budget.

    The limiter counts per address, in-process, and TestClient has no IP -- so
    every request from every test lands in one bucket. Across a suite this size
    that bucket fills, and tests start failing with 429 in whatever order they
    happen to run, which looks like flakiness rather than a shared counter.

    Clearing between tests is not a way of switching the limiter off: the
    middleware runs for every request in every test. It removes a dependency
    between tests that should not exist in the first place.
    """
    # Lazy, like restore_app_models below: an autouse fixture that imports an
    # application module makes every test in the repository depend on that module
    # importing cleanly. The backup and script tests deliberately touch no part of
    # the app, and importing it for them turned 36 of them into errors.
    import sys
    module = sys.modules.get("app.rate_limit")
    if module is None:
        yield
        return
    module.rate_limiter.requests.clear()
    yield
    module.rate_limiter.requests.clear()


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
    # Look in sys.modules; do not import. This teardown ran for every test, and
    # `from app.scheduler import scheduler` pulls in app.main with torch, SHAP and
    # transformers behind it -- more than the per-test timeout on a CI runner. The
    # body passed, the teardown was killed mid-import, the half-built module was
    # dropped from sys.modules, and the next test paid it again: PASSED-then-ERROR
    # at exactly the timeout, once per test, until the job was cancelled.
    #
    # A test that never imported app.scheduler cannot have started the scheduler.
    import sys
    module = sys.modules.get("app.scheduler")
    if module is None:
        return
    scheduler = getattr(module, "scheduler", None)
    if scheduler is None:
        return
    if scheduler.running:
        scheduler.remove_all_jobs()
        scheduler.shutdown(wait=False)
        raise AssertionError(
            "Test left the APScheduler running; shut it down in a finally block."
        )
