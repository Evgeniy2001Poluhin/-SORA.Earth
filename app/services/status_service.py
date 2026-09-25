"""Health ping recorder + uptime aggregation."""
from datetime import datetime, timedelta
from sqlalchemy import func
from app.database import SessionLocal, HealthPing
from app.api.system import _check_models, _check_db, _check_external_data

#: How often `record_health` is expected to run. `app/scheduler.py` registers
#: the `health_ping` job with this period written out, so `CLAUDE.md`'s job
#: table keeps telling an operator the period rather than a symbol name; the
#: two are pinned together by
#: tests/test_uptime_counts_the_window_not_the_samples.py.
HEALTH_PING_INTERVAL_MINUTES = 5

def _component_ok():
    """What this process can observe, and nothing it cannot.

    `api` was a literal `True`. The `health_ping` job in app/scheduler.py calls
    `record_health()` every five minutes from the **scheduler** container, which
    serves no HTTP and never contacts the API -- so every five minutes it wrote a
    row saying the API was healthy without looking. Measured on the local
    `health_pings` table: api 2653 of 2653 healthy, while `models` recorded
    false 2636 times through the same pipeline. The status page derives uptime
    from these rows, so API uptime could not fall below 100%.

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

def record_health():
    db = SessionLocal()
    try:
        for comp, ok in _component_ok().items():
            db.add(HealthPing(component=comp, ok=bool(ok)))
        db.commit()
    finally:
        db.close()

def _slots(db, comp, hours):
    """(up, observed, total) over slots one sample interval wide.

    A slot counts as up when it holds a sample and none of its samples says
    not-ok. A slot with no sample is **not** up: `record_health` runs inside the
    process being measured, so an outage leaves absence rather than `ok=False`,
    and a denominator of observed rows cannot see it. Measured over a 24-hour
    window with six hours holding no sample at all, the previous computation --
    ok rows over observed rows -- answered 100.0.

    Counting slots also collapses the on-request pings. `status_summary` records
    one on every request and the page refreshes every 30s, so a single open tab
    contributed 120 samples an hour against the scheduler's 12, every one of
    them taken at a moment the API was answering.
    """
    now = datetime.utcnow()
    since = now - timedelta(hours=hours)
    width = timedelta(minutes=HEALTH_PING_INTERVAL_MINUTES)
    total = max(1, int(timedelta(hours=hours) // width))
    seen = {}
    for ts, ok in db.query(HealthPing.ts, HealthPing.ok).filter(
            HealthPing.component == comp, HealthPing.ts >= since).all():
        if ts is None:
            continue
        idx = (ts - since) // width
        if idx < 0:
            continue
        # A sample dated after `now` -- clock skew between the two containers
        # that write here -- belongs to the newest slot rather than nowhere.
        idx = min(idx, total - 1)
        seen[idx] = seen.get(idx, True) and bool(ok)
    return sum(1 for up in seen.values() if up), len(seen), total

def _window(db, comp, hours):
    """The figure and how much of the window stands behind it.

    `uptime` is a **lower bound**: a slot nobody sampled counts against it. That
    is the convention this repository already applies at the promotion gate,
    where the 95% lower bound of AUC must clear the threshold rather than the
    point estimate, and it needs no threshold of its own.

    `coverage` is how much of the window was sampled at all, and the pair states
    a range without anybody choosing a cut-off: the true figure lies between
    `uptime` and `uptime + (100 - coverage)`. That matters because not every
    component has a sampler on a cadence -- the scheduler container stopped
    claiming `api`, which it cannot observe, so `api` rows now come only from
    somebody opening the page. A bare lower bound would report a healthy API at
    0.7%; the range says 0.7-100%, which is the truth about the evidence.
    """
    up, observed, total = _slots(db, comp, hours)
    if not observed:
        return {"uptime": None, "coverage": 0.0}
    return {"uptime": round(up / total * 100, 2),
            "coverage": round(observed / total * 100, 2)}

def _uptime(db, comp, hours):
    return _window(db, comp, hours)["uptime"]

def status_summary():
    record_health()  # on-request ping (covers dev where scheduler is off)
    db = SessionLocal()
    try:
        comps = ["api", "models", "database", "external_data"]
        cur = _component_ok()
        out = []
        for c in comps:
            day, week = _window(db, c, 24), _window(db, c, 24*7)
            out.append({"component": c, "ok": cur.get(c, False),
                        "uptime_24h": day["uptime"], "coverage_24h": day["coverage"],
                        "uptime_7d": week["uptime"], "coverage_7d": week["coverage"]})
        overall = all(x["ok"] for x in out)
        return {"overall": "operational" if overall else "degraded",
                "sample_interval_minutes": HEALTH_PING_INTERVAL_MINUTES,
                "components": out}
    finally:
        db.close()
