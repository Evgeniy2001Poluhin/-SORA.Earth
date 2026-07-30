"""The global request budget, exercised against the middleware itself.

Built on a throwaway app rather than app.main: importing the real one unpickles
models/*.pkl, so the test would then depend on Git LFS having been materialised,
which is a different question and has already broken CI once this week. The
middleware is the unit under test and it does not need the application.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.rate_limit import SlowAPIMiddleware, rate_limiter


@pytest.fixture
def app_with_limit(monkeypatch):
    """A small app behind the middleware, with a budget low enough to reach."""
    monkeypatch.setattr(rate_limiter, "max_requests", 5)
    rate_limiter.requests.clear()

    app = FastAPI()
    app.add_middleware(SlowAPIMiddleware)

    @app.get("/thing")
    def thing():
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/api/v1/model/retrain")
    def retrain():
        return {"ok": True}

    yield TestClient(app)
    rate_limiter.requests.clear()


def test_requests_are_refused_once_the_budget_is_spent(app_with_limit):
    codes = [app_with_limit.get("/thing").status_code for _ in range(7)]
    assert codes[:5] == [200] * 5, codes
    assert codes[5:] == [429, 429], codes


def test_the_refusal_says_how_long_to_wait(app_with_limit):
    for _ in range(5):
        app_with_limit.get("/thing")
    r = app_with_limit.get("/thing")

    assert r.status_code == 429
    # Without this a well-behaved client retries immediately and spends the next
    # window too, which turns one refusal into a sustained one.
    assert r.headers.get("Retry-After") == str(rate_limiter.window)
    assert r.json()["detail"] == "Rate limit exceeded"


def test_a_monitored_endpoint_is_never_refused(app_with_limit):
    """A liveness probe and a scrape share one address and arrive on a schedule.

    Spending the budget on them would make the health check flap, and a flapping
    health check reads as an outage.
    """
    codes = {app_with_limit.get("/health").status_code for _ in range(20)}
    assert codes == {200}


def test_an_expensive_path_counts_in_its_own_bucket(app_with_limit):
    """Otherwise the tighter number silently becomes the limit on everything."""
    for _ in range(5):
        assert app_with_limit.get("/thing").status_code == 200
    assert app_with_limit.get("/thing").status_code == 429

    # The general budget is spent; the retrain budget is untouched.
    assert app_with_limit.post("/api/v1/model/retrain").status_code == 200


def test_the_expensive_path_has_a_tighter_budget_of_its_own(monkeypatch):
    from app.rate_limit import PATH_LIMITS
    monkeypatch.setattr(rate_limiter, "max_requests", 100)
    rate_limiter.requests.clear()

    app = FastAPI()
    app.add_middleware(SlowAPIMiddleware)

    @app.post("/api/v1/model/retrain")
    def retrain():
        return {"ok": True}

    client = TestClient(app)
    limit = PATH_LIMITS["/api/v1/model/retrain"]
    codes = [client.post("/api/v1/model/retrain").status_code for _ in range(limit + 2)]

    assert codes[:limit] == [200] * limit, codes
    assert codes[limit:] == [429, 429], codes
    assert limit < 100, "the point of the entry is that it is tighter"
    rate_limiter.requests.clear()


def test_the_middleware_is_not_a_pass_through():
    """The regression this file exists for.

    SlowAPIMiddleware was installed in main.py, the handler and the limiter were
    wired up beside it, and it forwarded every request untouched -- while the
    documentation stated the limits as though they were enforced. A control that
    is believed in and absent is worse than no control.
    """
    import inspect
    source = inspect.getsource(SlowAPIMiddleware)
    assert "rate_limiter.check" in source, "the middleware does not consult the limiter"
    assert "Stub middleware" not in source
