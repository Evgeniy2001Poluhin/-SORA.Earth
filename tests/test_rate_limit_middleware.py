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


def test_an_expensive_path_has_its_own_bucket_as_well_as_the_general_one(app_with_limit):
    """Both budgets, not one of them.

    A tighter endpoint limit is an extra restriction on top of the general
    allowance, not an alternative to it. Charging only the endpoint bucket -- the
    first version of this -- let a caller spend the full general allowance and
    then the endpoint allowance on top, 110 requests where the documented limit
    was 100.

    Charging only the shared bucket is the mirror mistake: the tighter number
    then becomes the limit on everything.
    """
    for _ in range(5):
        assert app_with_limit.get("/thing").status_code == 200

    # The general budget is spent, so the expensive path is refused too, even
    # though its own budget has never been touched.
    assert app_with_limit.get("/thing").status_code == 429
    assert app_with_limit.post("/api/v1/model/retrain").status_code == 429


def test_a_refusal_costs_nothing(monkeypatch):
    """Two sequential checks do not compose.

    The general bucket would be charged before the endpoint bucket had decided,
    so a caller hammering a limited endpoint burned through their ordinary
    allowance while being told no. Every budget is examined before any is
    written to.
    """
    from app.rate_limit import rate_limiter as limiter
    monkeypatch.setattr(limiter, "max_requests", 20)
    limiter.requests.clear()

    app = FastAPI()
    app.add_middleware(SlowAPIMiddleware)

    @app.get("/thing")
    def thing():
        return {"ok": True}

    @app.post("/api/v1/model/retrain")
    def retrain():
        return {"ok": True}

    client = TestClient(app)
    from app.rate_limit import PATH_LIMITS
    endpoint_limit = PATH_LIMITS["/api/v1/model/retrain"]

    # Spend the endpoint budget, then keep going until it refuses.
    for _ in range(endpoint_limit):
        assert client.post("/api/v1/model/retrain").status_code == 200
    for _ in range(5):
        assert client.post("/api/v1/model/retrain").status_code == 429

    # Those five refusals must not have been charged to the general budget.
    used = len(limiter.requests.get("unknown", []))
    assert used == endpoint_limit, \
        f"the general bucket holds {used} hits; refused requests were charged to it"
    limiter.requests.clear()


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


def test_concurrent_requests_do_not_overspend_a_budget(monkeypatch):
    """The check and the record happen under one lock, so parallel callers cannot
    both read a bucket as having room and then both fill it."""
    import threading
    from app.rate_limit import rate_limiter as limiter, HTTPException

    monkeypatch.setattr(limiter, "max_requests", 50)
    limiter.requests.clear()

    allowed, refused = [], []
    barrier = threading.Barrier(20)

    def hit():
        barrier.wait()
        try:
            limiter.check_many([("same-caller", 50)])
            allowed.append(1)
        except HTTPException:
            refused.append(1)

    threads = [threading.Thread(target=hit) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(allowed) == 20, f"{len(refused)} refused inside the budget"
    assert len(limiter.requests["same-caller"]) == 20, \
        "a hit was lost or double-counted under contention"
    limiter.requests.clear()


def test_retry_after_reflects_the_bucket_that_blocks(monkeypatch):
    """A fixed window frees one slot at a time, so reporting the whole window is
    wrong in both directions -- too long when a slot is about to open, and too
    short for whichever budget actually governs when several block."""
    from app.rate_limit import rate_limiter as limiter, HTTPException

    monkeypatch.setattr(limiter, "max_requests", 2)
    limiter.requests.clear()
    for _ in range(2):
        limiter.check_many([("caller", 2)])

    with pytest.raises(HTTPException) as excinfo:
        limiter.check_many([("caller", 2)])

    wait = int(excinfo.value.headers["Retry-After"])
    assert 0 < wait <= limiter.window
    limiter.requests.clear()
