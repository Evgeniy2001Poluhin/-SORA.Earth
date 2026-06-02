"""Health ping recorder + uptime aggregation."""
from datetime import datetime, timedelta
from sqlalchemy import func
from app.database import SessionLocal, HealthPing
from app.api.system import _check_models, _check_db, _check_external_data

def _component_ok():
    out = {"api": True}
    try: out["models"] = _check_models().get("status") == "healthy"
    except Exception: out["models"] = False
    try: out["database"] = _check_db().get("status") == "healthy"
    except Exception: out["database"] = False
    try: out["external_data"] = _check_external_data().get("status") == "healthy"
    except Exception: out["external_data"] = False
    return out

def record_health():
    db = SessionLocal()
    try:
        for comp, ok in _component_ok().items():
            db.add(HealthPing(component=comp, ok=bool(ok)))
        db.commit()
    finally:
        db.close()

def _uptime(db, comp, hours):
    since = datetime.utcnow() - timedelta(hours=hours)
    rows = db.query(HealthPing.ok).filter(
        HealthPing.component == comp, HealthPing.ts >= since).all()
    if not rows:
        return None
    oks = [1 for (ok,) in rows if ok]
    return round(len(oks) / len(rows) * 100, 2)

def status_summary():
    record_health()  # on-request ping (covers dev where scheduler is off)
    db = SessionLocal()
    try:
        comps = ["api", "models", "database", "external_data"]
        cur = _component_ok()
        out = []
        for c in comps:
            out.append({"component": c, "ok": cur.get(c, False),
                        "uptime_24h": _uptime(db, c, 24), "uptime_7d": _uptime(db, c, 24*7)})
        overall = all(x["ok"] for x in out)
        return {"overall": "operational" if overall else "degraded", "components": out}
    finally:
        db.close()
