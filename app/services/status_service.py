"""Current component status (live checks, no persistence)."""
from app.api.system import _check_models, _check_db, _check_external_data

def _component_ok():
    """What this process can observe, and nothing it cannot.

    `api` was a literal `True`. The `health_ping` job in app/scheduler.py called
    `record_health()` every five minutes from the **scheduler** container, which
    served no HTTP and never contacted the API -- so every five minutes it wrote a
    row saying the API was healthy without looking. Measured on the local
    `health_pings` table: api 2653 of 2653 healthy, while `models` recorded
    false 2636 times through the same pipeline. The status page derived uptime
    from these rows, so API uptime could not fall below 100%. Both the job and
    `record_health()` were removed on 2026-09-25 (status page option C).

    The API process may still claim it: answering this call is the evidence.
    """
    from app.scheduler import should_run_scheduler

    out = {}
    if not should_run_scheduler():
        out["api"] = True
    try: out["models"] = _check_models().get("status") == "healthy"
    except Exception: out["models"] = False
    try: out["database"] = _check_db().get("status") == "healthy"
    except Exception: out["database"] = False
    try: out["external_data"] = _check_external_data().get("status") == "healthy"
    except Exception: out["external_data"] = False
    return out

def status_summary():
    """Current component status (live checks only, no database persistence).

    The uptime figures this used to return measured nothing: `api` rows came only
    from page views (the page calls this endpoint), so API uptime was 100% by
    construction. An open tab refreshing every 30s wrote 40 rows per 5 min,
    versus 3 from the scheduler -- the percentage was weighted by viewers, not
    availability. And it was a public GET that wrote to the database.

    Each component reports its live ok/down state, computed at request time. No
    rows are written. This function opens no session of its own, and the database
    and external-data checks (via `_component_ok()`) catch their own failures, so
    a database outage is reported as `database: ok=false` with a 200 instead of
    being raised.
    """
    comps = ["api", "models", "database", "external_data"]
    cur = _component_ok()
    out = []
    for c in comps:
        out.append({"component": c, "ok": cur.get(c, False)})
    overall = all(x["ok"] for x in out)
    return {"overall": "operational" if overall else "degraded", "components": out}
