import os
import time

import structlog
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Match

logger = structlog.get_logger("sora_earth")
START_TIME = time.time()

#: The most distinct endpoint keys `requests_by_endpoint` may hold.
#:
#: A bound independent of how the key is derived, because the derivation cannot
#: cover everything: a request that matches no route has no template, and 404s
#: are exactly what an attacker controls. With template keys alone the map is
#: bounded by the route table in the happy case and unbounded in the case that
#: matters.
MAX_ENDPOINT_KEYS = int(os.environ.get("SORA_MAX_ENDPOINT_KEYS", "200"))

#: A request that matched no route. One key for all of them, so a loop of
#: random URLs adds nothing.
UNMATCHED_KEY = "<unmatched>"

#: Everything counted after the bound was reached. Present so the total still
#: adds up: silently dropping counts would make the map look complete while
#: under-reporting whichever endpoints happened to arrive last.
OVERFLOW_KEY = "<over-cardinality-limit>"


def endpoint_key(request) -> str:
    """The route template this request matched, never the concrete path.

    `requests_by_endpoint` was keyed by `request.url.path`. Nine routes embed
    an identifier, so every session, evaluation, subscription and batch that
    was ever touched accumulated as its own key -- readable by anyone who could
    read the metrics, and growing by one key per distinct URL.

    Keying by the template removes both at once: there is no identifier in
    `/api/v1/evaluations/{evaluation_id}` to disclose, and the number of
    templates is fixed by the route table.

    The template is found by asking the router rather than by reading
    `scope["route"]`, and the reason is timing. Measured on starlette 0.49.3
    and fastapi under `BaseHTTPMiddleware`:

        before call_next    absent, always
        after call_next     an APIRoute under FastAPI; absent under a plain
                            starlette Route

    So the scope key is unusable where this is called. The count is recorded
    *before* `call_next` on purpose: a handler that raises still happened, and
    moving the record after the call to get a cheaper lookup would lose exactly
    the requests worth counting.

    Cost, measured rather than assumed: walking 166 routes and matching the
    last one takes 129 microseconds, which is 0.065% of the 200 ms p95 latency
    target. A cache keyed by concrete path would be faster and would reinstate
    the unbounded map this function exists to remove.

    The first measurement here was taken against a plain starlette app and said
    the key was absent in both positions. That is true of starlette and false
    of this application, which is the sort of difference a test catches and a
    reading does not.
    """
    app = request.scope.get("app")
    router = getattr(app, "router", None)
    if router is None:
        return UNMATCHED_KEY
    for route in getattr(router, "routes", ()):
        try:
            if route.matches(request.scope)[0] == Match.FULL:
                return getattr(route, "path", None) or UNMATCHED_KEY
        except Exception:
            # A route that cannot answer must not take the request down with
            # it; metrics are not worth a 500.
            continue
    return UNMATCHED_KEY


def record_endpoint(key: str) -> None:
    """Count one request against `key`, within the cardinality bound."""
    by_endpoint = METRICS["requests_by_endpoint"]
    if key in by_endpoint:
        by_endpoint[key] += 1
        return
    if len(by_endpoint) < MAX_ENDPOINT_KEYS:
        by_endpoint[key] = 1
        return
    by_endpoint[OVERFLOW_KEY] = by_endpoint.get(OVERFLOW_KEY, 0) + 1

METRICS = {
    "requests_total": 0, "predictions_total": 0, "errors_total": 0,
    "avg_response_time_ms": 0.0, "total_response_time_ms": 0.0,
    "evaluations_total": 0, "evaluations_avg_score": 0.0,
    "uptime_seconds": 0.0, "requests_by_endpoint": {}, "requests_by_status": {},
    "counters": {"http_requests_total": 0, "http_request": 0},
}

class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start = time.time()
        METRICS["requests_total"] += 1
        METRICS["counters"]["http_requests_total"] += 1
        METRICS["counters"]["http_request"] += 1
        path = request.url.path
        record_endpoint(endpoint_key(request))
        try:
            response = await call_next(request)
        except Exception:
            METRICS["errors_total"] += 1
            raise
        sk = str(response.status_code)
        METRICS["requests_by_status"][sk] = METRICS["requests_by_status"].get(sk, 0) + 1
        if response.status_code >= 400: METRICS["errors_total"] += 1
        elapsed_ms = (time.time() - start) * 1000
        METRICS["total_response_time_ms"] += elapsed_ms
        METRICS["avg_response_time_ms"] = round(METRICS["total_response_time_ms"] / METRICS["requests_total"], 2)
        METRICS["uptime_seconds"] = round(time.time() - START_TIME, 2)
        logger.info(f"{request.method} {path} {response.status_code} {elapsed_ms:.2f}ms")
        return response
