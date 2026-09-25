"""The status page shows only what it can measure.

Measured 2026-09-25: the public status page (/status in the SPA, data from
GET /api/v1/status/uptime) shows "Uptime 24h" and "Uptime 7d" per component,
but these figures measure nothing:

  - The `api` rows are written only by the page itself (status_summary() calls
    record_health() on every request), and the page can only load when the API
    is up -- so API uptime is 100% by construction.
  - An open tab refreshes every 30 s and writes 4 rows each time (40 rows per
    5 min), versus 3 rows per 5 min from the scheduler's health_ping job -- the
    percentage is dominated by whoever keeps the page open.
  - It is an unauthenticated GET that writes to the database, into health_pings,
    which has no retention.

The owner chose option C: remove the uptime figures and the writing to the
database; keep the current status (each component's live ok/down, computed at
request time).
"""
import json


def test_reading_the_status_page_writes_nothing(client):
    """status_summary() must not write to health_pings.

    The old code wrote 4 rows per request (one per component). The page itself
    calls the endpoint, so the "uptime" was weighted by viewers, not availability.
    """
    from app.database import SessionLocal, HealthPing

    db = SessionLocal()
    try:
        before = db.query(HealthPing).count()
        response = client.get("/api/v1/status/uptime")
        db.commit()  # ensure any writes are visible
        after = db.query(HealthPing).count()
    finally:
        db.close()

    assert response.status_code == 200
    assert after == before, f"status page wrote {after - before} rows to health_pings"


def test_a_write_would_be_visible_to_the_counter():
    """Control: prove the counter in test_reading_the_status_page_writes_nothing works.

    Without this, that test could pass because it counts the wrong database.
    """
    from datetime import datetime
    from app.database import SessionLocal, HealthPing

    db = SessionLocal()
    try:
        before = db.query(HealthPing).count()
        db.add(HealthPing(component="test", ok=True, ts=datetime.utcnow()))
        db.commit()
        after = db.query(HealthPing).count()

        assert after == before + 1, "counter did not see the inserted row"

        # Clean up: delete the test row so the table is left as it was found
        db.query(HealthPing).filter(HealthPing.component == "test").delete()
        db.commit()
        final = db.query(HealthPing).count()
        assert final == before, "failed to clean up test row"
    finally:
        db.close()


def test_the_response_carries_no_uptime_figure(client):
    """Each component's keys are exactly {"component", "ok"}.

    The old code added uptime_24h and uptime_7d; those are removed.
    """
    response = client.get("/api/v1/status/uptime")
    assert response.status_code == 200

    body = response.json()
    assert "components" in body

    for comp in body["components"]:
        assert set(comp.keys()) == {"component", "ok"}, \
            f"component {comp.get('component')} has unexpected keys: {comp.keys()}"
        # The string "uptime" must not appear anywhere in the JSON body's keys

    # Also check that "uptime" does not appear in any key name
    body_str = json.dumps(body)
    assert "uptime" not in body_str.lower(), \
        "the word 'uptime' appears in the response body"


def test_the_scheduler_registers_no_health_ping_job(monkeypatch):
    """The health_ping job is removed from the scheduler.

    The old code registered it every 5 minutes; it wrote rows the status page
    aggregated, but api rows came only from page views, so the percentage could
    not measure availability.
    """
    from apscheduler.schedulers.background import BackgroundScheduler
    import app.scheduler as scheduler_module

    before = {job.id for job in scheduler_module.scheduler.get_jobs()}

    isolated = BackgroundScheduler(timezone="UTC")
    monkeypatch.setattr(scheduler_module, "scheduler", isolated)
    monkeypatch.setenv("RUN_SCHEDULER", "true")
    monkeypatch.setenv("SORA_SCHEDULER", "1")

    try:
        scheduler_module.init_scheduler(start=False)
        registered = {job.id for job in isolated.get_jobs()}
    finally:
        if isolated.running:
            isolated.shutdown(wait=False)

    monkeypatch.undo()
    after = {job.id for job in scheduler_module.scheduler.get_jobs()}
    assert after == before, "this test changed the module scheduler"

    assert "health_ping" not in registered, \
        "health_ping job is still registered"

    # Control: prove the enumeration works by checking a known job
    assert "refresh_forecast_metrics" in registered, \
        "refresh_forecast_metrics job not found (proves enumeration is broken)"


def test_the_status_page_survives_a_database_outage(client, monkeypatch):
    """status_summary() must not open a database session.

    The old code opened SessionLocal() to write and read health_pings. After the
    change, the endpoint checks live component health without persisting anything,
    so a database outage must return 200 with database ok=false, not a 500.
    """
    # Make every SessionLocal import reachable from this route fail.
    # "from X import SessionLocal" binds a second name -- patching one does not
    # patch the other. Enumerate all of them:
    def _raising_session():
        raise RuntimeError("database is down")

    # Patch every module-level SessionLocal that status_summary can reach
    monkeypatch.setattr("app.database.SessionLocal", _raising_session, raising=False)
    monkeypatch.setattr("app.services.status_service.SessionLocal", _raising_session, raising=False)
    monkeypatch.setattr("app.api.system.SessionLocal", _raising_session, raising=False)
    monkeypatch.setattr("app.external_data.SessionLocal", _raising_session, raising=False)

    response = client.get("/api/v1/status/uptime")

    # The old code would fail with 500 here because status_summary() opens a session
    assert response.status_code == 200, \
        f"status page returned {response.status_code} during database outage (old code wrote to db)"

    body = response.json()
    assert body["overall"] == "degraded", \
        "overall status should be degraded when database is down"

    # Find the database component
    db_comp = next((c for c in body["components"] if c["component"] == "database"), None)
    assert db_comp is not None, "database component missing from response"
    assert db_comp["ok"] is False, \
        "database component should be ok=false during outage"
