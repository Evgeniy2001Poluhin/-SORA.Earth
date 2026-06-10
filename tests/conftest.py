import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture(scope="session")
def client():
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
