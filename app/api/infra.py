from app.prom_metrics import sora_retrain_total, sora_refresh_total, sora_full_pipeline_total
from fastapi import APIRouter, HTTPException, Request, Depends
from app.auth import require_admin
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest

from app.batch import BatchRequest, generate_batch_id
from app.database import get_db, BatchResultDB
from sqlalchemy.orm import Session
from datetime import datetime
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
def batch_evaluate(req: BatchRequest, db: Session = Depends(get_db)):
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
    status = "completed" if fail == 0 else ("partial" if success > 0 else "failed")

    import json as _json
    db_record = BatchResultDB(
        batch_id=batch_id,
        created_at=datetime.utcnow(),
        total=len(req.projects),
        successful=success,
        failed=fail,
        duration_ms=elapsed,
        status=status,
        results_json=_json.dumps(results),
        trigger_source="manual",
    )
    db.add(db_record)
    db.commit()

    return {
        "batch_id": batch_id,
        "total": len(req.projects),
        "successful": success,
        "failed": fail,
        "results": results,
        "processing_time_ms": elapsed,
    }


@router.get("/batch/{batch_id}", tags=["batch"])
def get_batch(batch_id: str, db: Session = Depends(get_db)):
    import json as _json
    record = db.query(BatchResultDB).filter(BatchResultDB.batch_id == batch_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Batch not found")
    return {
        "batch_id": record.batch_id,
        "total": record.total,
        "successful": record.successful,
        "failed": record.failed,
        "results": _json.loads(record.results_json) if record.results_json else [],
        "processing_time_ms": record.duration_ms,
        "status": record.status,
        "created_at": record.created_at.isoformat(),
    }


@router.get("/batch", tags=["batch"])
def list_batches(limit: int = 20, db: Session = Depends(get_db)):
    records = (
        db.query(BatchResultDB)
        .order_by(BatchResultDB.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "batch_id": r.batch_id,
            "total": r.total,
            "successful": r.successful,
            "failed": r.failed,
            "status": r.status,
            "created_at": r.created_at.isoformat(),
            "duration_ms": r.duration_ms,
        }
        for r in records
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
def check_drift_infra():
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
    """The Prometheus registry, same as `/metrics`.

    This used to assemble its own text from the in-process `METRICS` dict and
    never touch the registry, so the two Prometheus surfaces of one process
    disagreed about the same metric names. Measured in a single process at one
    instant (#94):

        /metrics                     sora_predictions_total{model="rf"} 7.0
        /api/v1/metrics/prometheus   sora_predictions_total 0

    Four names collided that way -- sora_predictions_total, sora_retrain_total,
    sora_refresh_total, sora_full_pipeline_total -- and none of the 22 metrics
    declared in app/prom_metrics.py appeared here at all. Setting a forecast
    gauge or a drift counter had no effect on this path, ever, while both paths
    returned 200 and plausible Prometheus text. The README, CLAUDE.md, the API
    catalogue and the Grafana notes all told the reader to curl this one and
    grep for metrics it could not contain.

    Serving the registry rather than deleting the route: nothing in the
    repository consumes this path, but eight documents name it, and every
    dashboard query already reads registry metrics, so the two agree by
    construction now instead of by coincidence.

    The operational counters this used to publish -- request counts by endpoint
    and status, uptime, average response time -- are unchanged on
    `/api/v1/metrics` as JSON, which is what they always were. Prometheus never
    saw them: infra/prometheus.yml scrapes `/metrics` and nothing else.
    """
    return PlainTextResponse(
        generate_latest(REGISTRY).decode("utf-8"),
        media_type=CONTENT_TYPE_LATEST,
    )
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



@router.get("/infra/data-refresh-status", tags=["infrastructure"])
def data_refresh_status():
    """Return latest data refresh log entries for UI display."""
    from app.database import SessionLocal, DataRefreshLog
    from sqlalchemy import desc

    db = SessionLocal()
    try:
        latest = (
            db.query(DataRefreshLog)
            .order_by(desc(DataRefreshLog.timestamp))
            .limit(10)
            .all()
        )
        return {
            "count": len(latest),
            "entries": [
                {
                    "id": r.id,
                    "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                    "status": r.status,
                    "countries_fetched": r.countries_fetched,
                    "total_countries": r.total_countries,
                    "message": r.message,
                }
                for r in latest
            ],
        }
    finally:
        db.close()


@router.post("/mlops/auto-retrain", tags=["infrastructure"])
def auto_retrain_on_drift(
    window: int = 50,
    min_samples: int = 50,
    force: bool = False,
    current_user=Depends(require_admin),
):
    """
    Closed-loop helper:
    1) check drift
    2) if drift detected (or force=true) -> run synchronous retrain
    3) return unified orchestration result
    """
    from app.api.drift import check_drift as drift_check
    from app.api.retrain import _do_retrain

    drift = drift_check(window=window)

    drift_detected = bool(drift.get("drift_detected", False))
    should_retrain = force or drift_detected

    if not should_retrain:
        return {
            "status": "ok",
            "drift_detected": False,
            "drift_result": drift,
            "retrained": False,
            "reason": "drift_not_detected",
        }

    # capture old AUC before retrain
    from app.api.retrain import _get_current_metrics
    old_metrics = _get_current_metrics()
    old_auc = old_metrics.get("auc_roc") or old_metrics.get("roc_auc")

    retrain_result = _do_retrain(min_samples=min_samples, trigger_source="mlops_auto")
    new_metrics = retrain_result.get("metrics", {}) if isinstance(retrain_result, dict) else {}
    new_auc = new_metrics.get("auc_roc") or new_metrics.get("roc_auc")

    # validate: reject if AUC dropped > 2%
    promoted = True
    reject_reason = None
    if old_auc is not None and new_auc is not None:
        auc_delta = float(new_auc) - float(old_auc)
        if auc_delta < -0.02:
            promoted = False
            reject_reason = "AUC degraded: %.4f -> %.4f (delta=%+.4f)" % (float(old_auc), float(new_auc), auc_delta)

    try:
        from app.database import SessionLocal, RetrainLog
        from sqlalchemy import desc
        import json

        db = SessionLocal()
        try:
            row = (
                db.query(RetrainLog)
                .filter(RetrainLog.trigger_source == "mlops_auto")
                .order_by(desc(RetrainLog.id))
                .first()
            )
            if row:
                existing = {}
                try:
                    existing = json.loads(row.metrics_json) if row.metrics_json else {}
                except Exception:
                    existing = {}
                existing.update({
                    "old_auc": float(old_auc) if old_auc is not None else None,
                    "new_auc": float(new_auc) if new_auc is not None else None,
                    "promoted": promoted,
                    "reject_reason": reject_reason,
                    "auc": float(new_auc) if new_auc is not None else existing.get("auc"),
                })
                row.metrics_json = json.dumps(existing, ensure_ascii=False)
                if promoted:
                    row.status = "promoted"
                else:
                    row.status = "rejected"
                db.commit()
        finally:
            db.close()
    except Exception:
        pass

    return {
        "status": "ok",
        "drift_detected": drift_detected,
        "drift_result": drift,
        "retrained": True,
        "forced": force,
        "promoted": promoted,
        "old_auc": float(old_auc) if old_auc else None,
        "new_auc": float(new_auc) if new_auc else None,
        "reject_reason": reject_reason,
        "retrain_result": retrain_result,
    }


@router.post("/mlops/full-pipeline", tags=["infrastructure"])
def run_full_pipeline(current_user=Depends(require_admin)):
    """Full MLOps pipeline: refresh → drift → retrain → AUC validate → promote/reject."""
    from app.scheduler import full_pipeline_run
    return full_pipeline_run(trigger_source="api_full_pipeline")


@router.post("/infra/data-refresh/run", tags=["infrastructure"])
def data_refresh_run():
    """
    Trigger full external ESG data refresh (World Bank/OECD + benchmarks).
    Returns aggregated result and writes audit record to DB.
    """
    from app.external_data import refresh_live_data
    from app.database import SessionLocal, DataRefreshLog

    db = SessionLocal()
    try:
        result = refresh_live_data(trigger_source="manual") or {}
        fetched = int(result.get("fetched") or 0)
        total = int(result.get("total") or 0)

        log = DataRefreshLog(
            status="success",
            countries_fetched=fetched,
            total_countries=total,
            message=None,
        )
        db.add(log)
        db.commit()

        return {
            "status": "ok",
            "fetched": fetched,
            "total": total,
        }
    except Exception as e:
        db.rollback()
        try:
            log = DataRefreshLog(
                status="failed",
                countries_fetched=0,
                total_countries=0,
                message=str(e)[:500],
            )
            db.add(log)
            db.commit()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"data refresh failed: {e}")
    finally:
        db.close()

