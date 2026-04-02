import time
import structlog
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = structlog.get_logger("sora_earth")
START_TIME = time.time()

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
        METRICS["requests_by_endpoint"][path] = METRICS["requests_by_endpoint"].get(path, 0) + 1
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
