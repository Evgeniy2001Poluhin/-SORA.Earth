from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.batch import BatchRequest, batch_history, generate_batch_id
from app.websocket import manager, WebSocket, WebSocketDisconnect
from app.cache import cache
from app.drift_detection import drift_detector
from app.mlflow_tracking import get_experiment_stats
from app.rate_limit import rate_limiter
from app.middleware import METRICS, START_TIME
from app.schemas import ProjectInput as Project

import time

from fastapi import Header, HTTPException, status
import os

ADMIN_TOKEN = os.getenv("SORA_ADMIN_TOKEN")

async def admin_auth(x_api_key: str = Header(..., alias="X-API-Key")):
    if not ADMIN_TOKEN or x_api_key != ADMIN_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

router = APIRouter()


# ===== BATCH =====
@router.post("/batch/evaluate", tags=["batch"])
def batch_evaluate(req: BatchRequest):
    from app.main import COUNTRIES, calculate_esg

    batch_id = generate_batch_id()
    start = time.time()
    results = []
    success = 0
    fail = 0
    for p in req.projects:
        try:
            project = Project(**{k: v for k, v in p.items() if k in Project.model_fields})
            cdata = COUNTRIES.get(project.region or "Germany", {"region": "Europe", "lat": 50.0, "lon": 10.0})
            region_name = cdata.get("region", "Europe")
            result = calculate_esg(project, region_name)
            result["project_name"] = project.name
            result["status"] = "success"
            results.append(result)
            success += 1
        except Exception as e:
            results.append({"project_name": p.get("name", "unknown"), "status": "error", "error": str(e)})
            fail += 1
    elapsed = round((time.time() - start) * 1000, 2)
    batch_result = {
        "batch_id": batch_id,
        "total": len(req.projects),
        "successful": success,
        "failed": fail,
        "results": results,
        "processing_time_ms": elapsed,
    }
    batch_history[batch_id] = batch_result
    return batch_result


@router.get("/batch/{batch_id}", tags=["batch"])
def get_batch(batch_id: str):
    if batch_id not in batch_history:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch_history[batch_id]


@router.get("/batch", tags=["batch"])
def list_batches():
    return [
        {"batch_id": k, "total": v["total"], "successful": v["successful"]}
        for k, v in batch_history.items()
    ]


# ===== WEBSOCKET =====
@router.websocket("/ws/live")
async def websocket_endpoint(ws: WebSocket):
    from app.auth import API_KEYS
    token = ws.query_params.get("token") or ws.headers.get("x-api-key")
    if token not in API_KEYS:
        await ws.close(code=1008)
        return
    await manager.connect(ws)
    try:
        while True:
            data = await ws.receive_text()
            await ws.send_json({"echo": data, "connections": manager.count})
    except WebSocketDisconnect:
        manager.disconnect(ws)


@router.get("/ws/status", tags=["websocket"])
def ws_status():
    return {"active_connections": manager.count}


# ===== CACHE =====
@router.get("/cache/stats", tags=["cache"])
def cache_stats():
    return cache.stats()


@router.post("/cache/clear", tags=["cache"])
def clear_cache():
    cache.clear()
    return {"status": "cache cleared"}


# ===== DRIFT =====
@router.get("/mlops/drift", tags=["mlops"])
def check_drift():
    return drift_detector.check_drift()


@router.get("/mlops/health", tags=["mlops"])
def mlops_health():
    drift = drift_detector.check_drift()
    return {
        "model_status": "healthy",
        "drift_status": drift["status"],
        "observations_tracked": drift["observations"],
        "monitoring": {
            "prometheus": "/metrics",
            "mlflow": "/mlflow/stats",
            "drift": "/mlops/drift",
        },
    }


# ===== MLFLOW =====
@router.get("/mlflow/stats", tags=["mlflow"])
def mlflow_stats():
    return get_experiment_stats()


# ===== RATE LIMIT =====
@router.get("/rate-limit/status", tags=["monitoring"])
def rate_limit_status(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    return rate_limiter.get_usage(client_ip)


# ===== UNIFIED METRICS =====
@router.get("/metrics")
async def get_metrics():
    METRICS["uptime_seconds"] = round(time.time() - START_TIME, 2)
    return METRICS


@router.get("/system/metrics")
async def get_system_metrics():
    return METRICS


@router.get("/metrics/prometheus")
async def prometheus_metrics():
    import time

    METRICS["uptime_seconds"] = round(time.time() - START_TIME, 2)
    m = METRICS

    lines = [
        "# HELP sora_requests_total Total HTTP requests",
        "# TYPE sora_requests_total counter",
        f'sora_requests_total {m["requests_total"]}',

        "# HELP sora_predictions_total Total predictions",
        "# TYPE sora_predictions_total counter",
        f'sora_predictions_total {m["predictions_total"]}',

        "# HELP sora_errors_total Total error responses",
        "# TYPE sora_errors_total counter",
        f'sora_errors_total {m["errors_total"]}',

        "# HELP sora_avg_response_time_ms Average response time in milliseconds",
        "# TYPE sora_avg_response_time_ms gauge",
        f'sora_avg_response_time_ms {m["avg_response_time_ms"]}',

        "# HELP sora_total_response_time_ms Total accumulated response time in milliseconds",
        "# TYPE sora_total_response_time_ms counter",
        f'sora_total_response_time_ms {m.get("total_response_time_ms", 0)}',

        "# HELP sora_evaluations_total Total ESG evaluations",
        "# TYPE sora_evaluations_total counter",
        f'sora_evaluations_total {m.get("evaluations_total", 0)}',

        "# HELP sora_evaluations_avg_score Average ESG evaluation score",
        "# TYPE sora_evaluations_avg_score gauge",
        f'sora_evaluations_avg_score {m.get("evaluations_avg_score", 0.0)}',

        "# HELP sora_uptime_seconds Application uptime in seconds",
        "# TYPE sora_uptime_seconds gauge",
        f'sora_uptime_seconds {m.get("uptime_seconds", 0.0)}',
    ]

    for ep, count in m["requests_by_endpoint"].items():
        ep_escaped = str(ep).replace("\\", "\\\\").replace('"', '\\"')
        lines += [
            "# HELP sora_requests_by_endpoint Requests count by HTTP endpoint",
            "# TYPE sora_requests_by_endpoint gauge",
            f'sora_requests_by_endpoint{{path="{ep_escaped}"}} {count}',
        ]

    for st, count in m["requests_by_status"].items():
        st_escaped = str(st).replace("\\", "\\\\").replace('"', '\\"')
        lines += [
            "# HELP sora_requests_by_status Requests count by HTTP status code",
            "# TYPE sora_requests_by_status gauge",
            f'sora_requests_by_status{{status="{st_escaped}"}} {count}',
        ]

    return PlainTextResponse("\n".join(lines), media_type="text/plain; version=0.0.4")
# --- Redis Cache ---
from app.redis_cache import cache_stats as redis_stats, cache_get, cache_set, REDIS_AVAILABLE

@router.get('/cache/redis', summary='Redis cache stats')
def get_redis_stats():
    return redis_stats()

@router.get('/cache/redis/test', summary='Test Redis cache')
def test_redis():
    cache_set('test_key', {'status': 'ok', 'source': 'sora'}, ttl=60)
    result = cache_get('test_key')
    return {'redis_available': REDIS_AVAILABLE, 'test_result': result}

@router.delete('/cache/redis/invalidate', summary='Invalidate all prediction cache')
def invalidate_cache():
    from app.redis_cache import redis_client
    if not REDIS_AVAILABLE:
        return {'cleared': 0, 'error': 'Redis unavailable'}
    keys = redis_client.keys('sora:*')
    if keys:
        redis_client.delete(*keys)
    return {'cleared': len(keys), 'keys': keys}

@router.delete('/cache/redis/invalidate/{prefix}', summary='Invalidate cache by prefix')
def invalidate_cache_prefix(prefix: str):
    from app.redis_cache import redis_client
    if not REDIS_AVAILABLE:
        return {'cleared': 0, 'error': 'Redis unavailable'}
    pattern = 'sora:' + prefix + ':*'
    keys = redis_client.keys(pattern)
    if keys:
        redis_client.delete(*keys)
    return {'cleared': len(keys), 'prefix': prefix, 'keys': keys}

