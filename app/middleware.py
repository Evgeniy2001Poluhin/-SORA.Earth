from app.metrics import metrics as app_metrics
import time
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("sora_earth")

METRICS = {
    "requests_total": 0,
    "requests_by_endpoint": {},
    "requests_by_status": {},
    "avg_response_time_ms": 0,
    "total_response_time_ms": 0,
    "predictions_total": 0,
    "errors_total": 0,
}

class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration_ms = round((time.time() - start) * 1000, 2)

        path = request.url.path
        status = response.status_code

        METRICS["requests_total"] += 1
        METRICS["total_response_time_ms"] += duration_ms
        METRICS["avg_response_time_ms"] = round(
            METRICS["total_response_time_ms"] / METRICS["requests_total"], 2
        )
        METRICS["requests_by_endpoint"][path] = METRICS["requests_by_endpoint"].get(path, 0) + 1
        METRICS["requests_by_status"][str(status)] = METRICS["requests_by_status"].get(str(status), 0) + 1

        if ("predict" in path or "evaluate" in path) and request.method == "POST":
            METRICS["predictions_total"] += 1

        if status >= 400:
            METRICS["errors_total"] += 1

        app_metrics.inc("http_requests_total")
        app_metrics.inc(f"http_{status}")
        app_metrics.observe("request_duration", duration_ms)
        logger.info(f"{request.method} {path} {status} {duration_ms}ms")
        return response
