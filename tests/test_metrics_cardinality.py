"""The metrics map holds route templates, and a bounded number of them.

`requests_by_endpoint` was keyed by `request.url.path`. Nine routes embed an
identifier, so every session, evaluation, subscription and batch that had ever
been touched accumulated as its own key -- and the map grew by one key per
distinct URL, which a caller controls.

Two independent fixes, and both are needed:

    template keying   removes the identifiers. Bounded by the route table in
                      the happy case.
    a hard bound      covers the case that matters. A request matching no route
                      has no template, and 404s are exactly what an attacker
                      can mint.

These run against the real middleware over a small app rather than through the
platform, because the property is about what the middleware writes, not about
any particular route.
"""
import copy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware import (
    METRICS,
    OVERFLOW_KEY,
    UNMATCHED_KEY,
    MetricsMiddleware,
    endpoint_key,
    record_endpoint,
)


@pytest.fixture(autouse=True)
def pristine_metrics():
    """`METRICS` is a process-global, and every test here writes to it.

    Clearing `requests_by_endpoint` is not enough: a request through the
    middleware also moves `requests_total`, the counters, the timings and
    `requests_by_status`, so a later test reading any of those would be seeing
    this file's traffic. Deep, because the values are dicts and a shallow copy
    would restore references to the mutated ones.

    The per-test clears below stay: they make each case start from a known map
    rather than from whatever the previous one left, which is a different job.
    """
    saved = copy.deepcopy(METRICS)
    yield
    METRICS.clear()
    METRICS.update(saved)


@pytest.fixture
def app_with_metrics(monkeypatch):
    """A two-route app carrying the real middleware, and a clean map."""
    METRICS["requests_by_endpoint"].clear()

    app = FastAPI()
    app.add_middleware(MetricsMiddleware)

    @app.get("/items/{item_id}")
    def item(item_id: str):
        return {"id": item_id}

    @app.get("/plain")
    def plain():
        return {"ok": True}

    yield TestClient(app, raise_server_exceptions=False)
    METRICS["requests_by_endpoint"].clear()


def _keys():
    return set(METRICS["requests_by_endpoint"])


# --- identifiers do not enter the map ----------------------------------------


def test_two_identifiers_collapse_to_one_key(app_with_metrics):
    app_with_metrics.get("/items/session-abc")
    app_with_metrics.get("/items/session-def")

    assert _keys() == {"/items/{item_id}"}
    assert METRICS["requests_by_endpoint"]["/items/{item_id}"] == 2


def test_no_identifier_survives_anywhere_in_the_map(app_with_metrics):
    """The disclosure itself, stated as the absence of the secret.

    Asserting the key set alone would pass if the identifier appeared in some
    other key -- this looks at the whole map as text.
    """
    app_with_metrics.get("/items/tok_A1B2C3_unguessable")

    assert "tok_A1B2C3_unguessable" not in str(METRICS["requests_by_endpoint"])


def test_a_path_matching_no_route_is_one_key_for_all_of_them(app_with_metrics):
    for n in range(50):
        app_with_metrics.get(f"/no-such-thing/{n}")

    assert _keys() == {UNMATCHED_KEY}
    assert METRICS["requests_by_endpoint"][UNMATCHED_KEY] == 50


def test_a_static_route_keeps_its_own_key(app_with_metrics):
    """Templating must not collapse distinct endpoints into one another."""
    app_with_metrics.get("/plain")
    app_with_metrics.get("/items/x")

    assert _keys() == {"/plain", "/items/{item_id}"}


# --- the bound ---------------------------------------------------------------


def test_the_map_stops_growing_at_the_bound(monkeypatch):
    monkeypatch.setattr("app.middleware.MAX_ENDPOINT_KEYS", 5)
    METRICS["requests_by_endpoint"].clear()
    try:
        for n in range(100):
            record_endpoint(f"/route-{n}")

        # Five real keys plus the overflow bucket, and nothing else.
        assert len(METRICS["requests_by_endpoint"]) == 6
    finally:
        METRICS["requests_by_endpoint"].clear()


def test_counts_past_the_bound_are_kept_not_dropped(monkeypatch):
    """Silently dropping would make the map look complete while under-reporting
    whichever endpoints happened to arrive last."""
    monkeypatch.setattr("app.middleware.MAX_ENDPOINT_KEYS", 2)
    METRICS["requests_by_endpoint"].clear()
    try:
        for n in range(10):
            record_endpoint(f"/route-{n}")

        counted = sum(METRICS["requests_by_endpoint"].values())
        assert counted == 10, METRICS["requests_by_endpoint"]
        assert METRICS["requests_by_endpoint"][OVERFLOW_KEY] == 8
    finally:
        METRICS["requests_by_endpoint"].clear()


def test_a_key_already_present_still_counts_past_the_bound(monkeypatch):
    """The bound is on distinct keys, not on requests.

    Written because the obvious implementation -- refuse every write once the
    map is full -- would stop counting the endpoints that matter most, which
    are the ones already there.
    """
    monkeypatch.setattr("app.middleware.MAX_ENDPOINT_KEYS", 2)
    METRICS["requests_by_endpoint"].clear()
    try:
        record_endpoint("/a")
        record_endpoint("/b")
        for _ in range(5):
            record_endpoint("/a")

        assert METRICS["requests_by_endpoint"]["/a"] == 6
    finally:
        METRICS["requests_by_endpoint"].clear()


# --- how the template is found ----------------------------------------------


def test_the_route_is_absent_from_the_scope_when_the_key_is_needed():
    """Why `endpoint_key` asks the router instead of reading the scope.

    Measured, and the first measurement was wrong. Against a plain starlette
    app `scope["route"]` is absent both before and after `call_next`; against
    this application, which is FastAPI, it is an APIRoute *after* the call. The
    difference is invisible to a reading and decides whether the simpler
    implementation works.

    What matters here is the position the count is taken at. It is recorded
    before `call_next`, because a handler that raises still happened -- and
    there the key is absent under both frameworks. Moving the record later to
    use the scope would lose exactly the requests worth counting.
    """
    seen = {}

    app = FastAPI()

    class Probe(MetricsMiddleware):
        async def dispatch(self, request, call_next):
            seen["before"] = request.scope.get("route")
            response = await super().dispatch(request, call_next)
            seen["after"] = request.scope.get("route")
            return response

    app.add_middleware(Probe)

    @app.get("/probe/{x}")
    def probe(x: str):
        return {}

    METRICS["requests_by_endpoint"].clear()
    TestClient(app).get("/probe/1")

    assert seen["before"] is None, (
        "scope['route'] is set before call_next after all; endpoint_key could "
        "read it and skip the router walk"
    )
    assert seen["after"] is not None, (
        "FastAPI stopped populating scope['route']; the docstring in "
        "app/middleware.py records the opposite and is now wrong"
    )
    assert "/probe/{x}" in METRICS["requests_by_endpoint"]
    METRICS["requests_by_endpoint"].clear()


def test_an_endpoint_that_raises_is_still_counted():
    """The reason the count is taken before `call_next`."""
    app = FastAPI()
    app.add_middleware(MetricsMiddleware)

    @app.get("/boom/{x}")
    def boom(x: str):
        raise RuntimeError("handler failed")

    METRICS["requests_by_endpoint"].clear()
    try:
        TestClient(app, raise_server_exceptions=False).get("/boom/secret-id")

        assert METRICS["requests_by_endpoint"].get("/boom/{x}") == 1
        assert "secret-id" not in str(METRICS["requests_by_endpoint"])
    finally:
        METRICS["requests_by_endpoint"].clear()


def test_a_method_mismatch_keeps_the_template(app_with_metrics):
    """A 405 is `/items/{item_id}`, not `<unmatched>`.

    `route.matches` answers PARTIAL when the path matches and the method does
    not. Accepting only FULL threw that away, so a burst of method-mismatch
    requests against one route was indistinguishable from random URLs -- and
    the operator lost the one fact that would have identified the caller's
    mistake.

    Bounded either way: PARTIAL still yields a template from the fixed route
    table, so this cannot be used to mint keys.
    """
    response = app_with_metrics.post("/items/abc")

    assert response.status_code == 405
    assert "/items/{item_id}" in METRICS["requests_by_endpoint"]
    assert UNMATCHED_KEY not in METRICS["requests_by_endpoint"]


def test_a_full_match_still_wins_over_an_earlier_partial(app_with_metrics):
    """FULL can appear later in the table than a PARTIAL for the same path.

    Taking the first partial and returning immediately would key a served
    request as whatever route happened to be declared first.
    """
    app_with_metrics.get("/items/abc")

    assert METRICS["requests_by_endpoint"] == {"/items/{item_id}": 1}
