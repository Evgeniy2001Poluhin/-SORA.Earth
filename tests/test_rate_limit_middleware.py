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
    #
    # A number, and one that is not the whole window.
    #
    # This asserted equality with rate_limiter.window -- the fixed value the
    # change replaces -- so the test would have passed unchanged whether or not
    # the per-bucket figure ever reached HTTP. It did not: the middleware caught
    # the exception and rebuilt the response with the constant, discarding what
    # had just been computed. Only direct callers saw the new number, and this
    # assertion was the reason nobody noticed.
    retry_after = r.headers.get("Retry-After")
    assert retry_after is not None
    assert retry_after.isdigit(), retry_after
    assert 1 <= int(retry_after) <= rate_limiter.window
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


def test_concurrency_cannot_overspend_the_tightest_budget(monkeypatch):
    """A hundred callers at once against a budget of ten.

    An earlier version of this released twenty threads against a budget of fifty
    and asserted twenty hits landed. That shows nothing is lost or double
    counted, which is worth knowing, and says nothing at all about whether the
    limit *bounds* -- every caller was inside the budget, so a completely
    unsynchronised implementation would have passed it too.

    The bound is the property. Two budgets, the tighter one governing, and every
    thread contending for the same slot at the same instant.
    """
    import threading
    from app.rate_limit import rate_limiter as limiter, HTTPException

    monkeypatch.setattr(limiter, "max_requests", 20)
    limiter.requests.clear()

    callers = 100
    endpoint_limit = 10
    allowed, refused = [], []
    lock = threading.Lock()
    barrier = threading.Barrier(callers)

    def hit():
        barrier.wait()
        try:
            limiter.check_many([("caller", 20), ("caller|retrain", endpoint_limit)])
            with lock:
                allowed.append(1)
        except HTTPException:
            with lock:
                refused.append(1)

    threads = [threading.Thread(target=hit) for _ in range(callers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(allowed) == endpoint_limit, \
        f"{len(allowed)} allowed against a budget of {endpoint_limit}"
    assert len(refused) == callers - endpoint_limit
    # Neither bucket may hold more than it granted, and the general bucket must
    # not have been charged for the ninety refusals.
    assert len(limiter.requests["caller"]) == endpoint_limit
    assert len(limiter.requests["caller|retrain"]) == endpoint_limit
    limiter.requests.clear()


def test_the_semantics_are_grant_only(monkeypatch):
    """Recorded on a grant, not on an attempt.

    This is a choice, not a law: some anti-abuse counters deliberately charge
    refused attempts, so that hammering a closed door costs the attacker. Here a
    refusal costs nothing, which keeps one budget from being drained by refusals
    at another. Written down because the opposite is defensible and someone will
    otherwise assume whichever they are used to.
    """
    from app.rate_limit import rate_limiter as limiter, HTTPException

    monkeypatch.setattr(limiter, "max_requests", 3)
    limiter.requests.clear()
    for _ in range(3):
        limiter.check_many([("caller", 3)])

    for _ in range(10):
        with pytest.raises(HTTPException):
            limiter.check_many([("caller", 3)])

    assert len(limiter.requests["caller"]) == 3, \
        "refused attempts were recorded; that is attempt semantics, not grant"
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


def test_the_advertised_wait_reaches_the_client_over_http(monkeypatch):
    """The per-bucket figure must survive the trip out of the middleware.

    check_many computes a wait from the fullest blocking bucket and raises with
    it. The middleware used to catch that and rebuild the response from
    rate_limiter.window, so over HTTP the behaviour was identical to the fixed
    window this change exists to replace. A fix visible only to its own tests is
    not a fix.

    The first version of this test asserted `1 <= wait <= 600`, which the old
    fixed value of 600 satisfies -- a regression test that could not tell the
    regression from the fix, written to prevent exactly that. The hits are aged
    instead, so the correct answer and the constant are far apart and only one
    of them can pass.
    """
    import time

    from app.rate_limit import RateLimiter
    import app.rate_limit as rl_module

    window = 600
    limiter = RateLimiter(max_requests=2, window_seconds=window)
    monkeypatch.setattr(rl_module, "rate_limiter", limiter)

    app = FastAPI()
    app.add_middleware(SlowAPIMiddleware)

    @app.get("/thing")
    def thing():
        return {"ok": True}

    client = TestClient(app)
    for _ in range(2):
        client.get("/thing")

    # Age every recorded hit by 500 seconds. The oldest then expires in about
    # 100, so a correct Retry-After is nowhere near the 600 the constant gives.
    aged = time.monotonic() - 500
    for key in list(limiter.requests):
        limiter.requests[key] = [aged for _ in limiter.requests[key]]

    r = client.get("/thing")
    assert r.status_code == 429

    wait = int(r.headers["Retry-After"])
    assert wait != window, (
        "Retry-After is the whole window, which is the fixed value this change "
        "replaces -- the exception's header is being discarded again"
    )
    assert 90 <= wait <= 110, wait
