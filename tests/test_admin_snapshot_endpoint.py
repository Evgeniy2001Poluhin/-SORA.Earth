"""`GET /api/v1/admin/snapshot` — the endpoint whose cutoff was changed.

The timezone fix altered a live route and was covered only at the model layer:
the queries were tested, the endpoint that issues them was not. Searching the
suite for it turned up one reference, in `tests/locustfile.py` — a load script,
not a check. So this file exists because the change had no test where the change
was made.

Two things are asserted: that the route still answers with its schema intact,
and that the 48-hour liveness window it computes actually discriminates — a
recent row makes the scheduler read as running, an old one does not. Without the
second, the endpoint could return a constant and still pass.
"""

from datetime import datetime, timedelta, timezone

import pytest

def _admin_token(client):
    """A real JWT. require_admin checks a role in the token, not SORA_ADMIN_TOKEN.

    The same login the other admin tests use -- an earlier version of this file
    sent the admin env var as a bearer token and got `Invalid token format`.
    """
    response = client.post(
        "/api/v1/auth/login-json", json={"username": "admin", "password": "sora2026"})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture
def refresh_log_rows():
    """Insert rows, and take them out again whatever the test does."""
    from app.database import DataRefreshLog, SessionLocal

    inserted = []

    # The liveness fallback returns True if ANY row is inside 48 hours, so a
    # leftover from another test would make the negative case pass for the wrong
    # reason. The table is cleared first, and restored to empty afterwards.
    with SessionLocal() as session:
        session.query(DataRefreshLog).delete(synchronize_session=False)
        session.commit()

    def _add(started_at):
        with SessionLocal() as session:
            row = DataRefreshLog(status="ok", job_name="test-liveness", started_at=started_at)
            session.add(row)
            session.commit()
            inserted.append(row.id)

    yield _add

    with SessionLocal() as session:
        if inserted:
            session.query(DataRefreshLog).filter(DataRefreshLog.id.in_(inserted)).delete(
                synchronize_session=False)
            session.commit()


def _get(client):
    return client.get(
        "/api/v1/admin/snapshot",
        headers={"Authorization": "Bearer %s" % _admin_token(client)})


def test_the_route_answers(client, refresh_log_rows):
    response = _get(client)
    assert response.status_code == 200, response.text
    body = response.json()
    # The five sections AdminSnapshot declares, named from the model rather than
    # guessed -- the first version of this assertion looked for "retrain", which
    # is not a field, and passed a 200 response off as a schema check.
    for section in ("model", "data", "drift", "scheduler", "retrain_log_summary"):
        assert section in body, "%s missing from the response: %s" % (section, sorted(body))


def test_a_recent_refresh_reads_as_a_running_scheduler(client, refresh_log_rows):
    """The half of the window that must say yes."""
    refresh_log_rows(datetime.now(timezone.utc) - timedelta(hours=1))
    body = _get(client).json()
    assert body.get("scheduler", {}).get("running") is True, body.get("scheduler")


def test_an_old_refresh_does_not(client, refresh_log_rows):
    """And the half that must say no, or the check above proves nothing.

    72 hours is outside the 48-hour window by a margin larger than any plausible
    timezone offset, so this stays true wherever the database session sits.
    """
    refresh_log_rows(datetime.now(timezone.utc) - timedelta(hours=72))
    body = _get(client).json()
    assert body.get("scheduler", {}).get("running") is not True, body.get("scheduler")


def test_it_refuses_without_the_admin_token(client):
    """The route is admin-only; asserted here so the fixture above cannot hide it."""
    response = client.get("/api/v1/admin/snapshot")
    assert response.status_code in (401, 403), response.status_code
