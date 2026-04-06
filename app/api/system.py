"""System health and readiness endpoints."""
import time, os, platform
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

router = APIRouter(tags=["System"])
START_TIME = time.time()
VERSION = "2.0.0"

def _check_models():
    try:
        from app.main import rf_model, xgb_model, nn_model, DB_PATH
        ok = all([rf_model is not None, xgb_model is not None, nn_model is not None])
        return {"status": "healthy" if ok else "degraded", "loaded": ok}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}

def _check_db():
    try:
        import sqlite3
        conn = sqlite3.connect(DB_PATH, timeout=2)  # pragma: no cover
        conn.execute("SELECT 1")  # pragma: no cover
        conn.close()  # pragma: no cover
        return {"status": "healthy"}  # pragma: no cover
    except Exception as e:
        return {"status": "degraded", "error": str(e)}

def _check_external_data():
    try:
        from app.external_data import get_refresh_status
        count = get_refresh_status().get("static_countries", 0)
        return {"status": "healthy" if count >= 20 else "degraded", "countries_loaded": count}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}

@router.get("/health", summary="Full health check")
async def health_check():
    checks = {
        "models": _check_models(),
        "database": _check_db(),
        "external_data": _check_external_data(),
    }
    ok = all(c["status"] != "unhealthy" for c in checks.values())
    return JSONResponse(
        content={
            "status": "healthy" if ok else "degraded",
            "version": VERSION,
            "uptime_seconds": round(time.time() - START_TIME),
            "python": platform.python_version(),
            "checks": checks,
        },
        status_code=200 if ok else 503,
    )

@router.get("/ready", summary="Readiness probe")
async def readiness():
    m = _check_models()
    if m["status"] != "healthy":
        return JSONResponse({"status": "not_ready", "reason": m}, status_code=503)  # pragma: no cover
    return {"status": "ready", "version": VERSION}

@router.get("/ping", include_in_schema=False)
async def ping():
    return {"pong": True}
