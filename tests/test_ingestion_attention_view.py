"""The run record gets a reader, and it is ordered by what to do.

#74. `ingester_runs` was written on every run and read by nothing -- not the
API, not the frontend, not a dashboard. A verdict nobody can read is the same
as no verdict, which is how 333 consecutive empty openaq runs went unnoticed
(#56, #57).

Ordering is the substance here, not decoration: a list sorted by time buries
the row an operator is looking for under whatever ran most recently.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.database import IngesterRun, SessionLocal


@pytest.fixture(autouse=True)
def as_admin(client):
    """The endpoint is admin-only: `failure_reason` carries exception text.

    Overriding the dependency rather than minting a token keeps these tests
    about the view. tests/test_ingestion_attention_auth.py covers the refusal.
    """
    from app.auth import require_admin
    from app.main import app

    app.dependency_overrides[require_admin] = lambda: {"username": "t", "role": "admin"}
    yield
    app.dependency_overrides.pop(require_admin, None)


@pytest.fixture
def runs():
    """Rows written directly, so the view is tested and not the runner."""
    db = SessionLocal()
    try:
        db.query(IngesterRun).delete()
        db.commit()
        yield db
        db.query(IngesterRun).delete()
        db.commit()
    finally:
        db.close()


def _add(db, source, action, *, reason="source_empty", status="degraded",
         age=None, minutes_ago=1, finished=True):
    now = datetime.now(timezone.utc)
    db.add(IngesterRun(
        source=source,
        started_at=now - timedelta(minutes=minutes_ago + 1),
        finished_at=(now - timedelta(minutes=minutes_ago)) if finished else None,
        status=status, reason_code=reason, required_action=action,
        source_vintage_seconds=age, records_received=0, records_accepted=0,
        records_rejected=0,
    ))
    db.commit()


def test_escalate_outranks_a_more_recent_wait(client, runs):
    """The ordering claim, with time pointing the other way.

    The `wait` row is newer, so any sort that falls back to time puts it first.
    """
    _add(runs, "openaq", "escalate", age=9 * 365 * 86400, minutes_ago=600)
    _add(runs, "openmeteo", "wait", age=60, minutes_ago=1)

    body = client.get("/api/v1/ingestion/attention").json()

    assert [r["source"] for r in body["sources"]] == ["openaq", "openmeteo"]


def test_every_action_is_ordered(client, runs):
    _add(runs, "a", "none", reason="ok", status="success")
    _add(runs, "b", "wait")
    _add(runs, "c", "investigate")
    _add(runs, "d", "escalate")

    body = client.get("/api/v1/ingestion/attention").json()

    assert [r["source"] for r in body["sources"]] == ["d", "c", "b", "a"]
    assert body["count"] == 4
    assert body["needs_attention"] == 3


def test_an_unreadable_action_sorts_first(client, runs):
    """A row whose own verdict is missing is not one to scroll past.

    Runs recorded before the columns existed are NULL, and so is any run whose
    write of them failed. Sorting those to the bottom would hide exactly the
    record that cannot be trusted.
    """
    _add(runs, "escalating", "escalate", age=99999)
    _add(runs, "legacy", None, reason=None)

    body = client.get("/api/v1/ingestion/attention").json()

    assert body["sources"][0]["source"] == "legacy"
    assert body["sources"][0]["required_action"] is None


def test_only_the_latest_run_per_source_appears(client, runs):
    """The question is what the state is now, not what it has been."""
    _add(runs, "openaq", "escalate", minutes_ago=120)
    _add(runs, "openaq", "wait", age=60, minutes_ago=1)

    body = client.get("/api/v1/ingestion/attention").json()

    assert body["count"] == 1
    assert body["sources"][0]["required_action"] == "wait"


def test_a_run_still_going_is_not_reported_as_the_state(client, runs):
    """`finished_at IS NULL` means it has not decided anything yet.

    Without the filter, a run that started thirty seconds ago replaces the last
    completed verdict with a row whose action is NULL -- which then sorts to the
    top as unreadable, every time anything is running.
    """
    _add(runs, "openaq", "escalate", age=9 * 365 * 86400, minutes_ago=10)
    _add(runs, "openaq", None, reason=None, status="running", finished=False,
         minutes_ago=0)

    body = client.get("/api/v1/ingestion/attention").json()

    assert body["count"] == 1
    assert body["sources"][0]["required_action"] == "escalate"


def test_an_empty_table_is_an_empty_answer_not_an_error(client, runs):
    body = client.get("/api/v1/ingestion/attention").json()

    assert body == {"count": 0, "needs_attention": 0, "sources": []}


def test_the_row_carries_what_the_decision_was_made_from(client, runs):
    """An action without its inputs is a verdict to be taken on faith."""
    _add(runs, "openaq", "escalate", reason="source_empty",
         age=9 * 365 * 86400)

    row = client.get("/api/v1/ingestion/attention").json()["sources"][0]

    for field in ("reason_code", "source_vintage_seconds", "max_vintage_seconds",
                  "freshness_status", "records_received",
                  "records_accepted", "records_rejected", "status",
                  "finished_at"):
        assert field in row, field
    assert row["reason_code"] == "source_empty"
    assert row["source_vintage_seconds"] == pytest.approx(9 * 365 * 86400)


def test_the_last_run_to_finish_wins_not_the_largest_id(client, runs):
    """id follows start order; two runs can finish in the reverse of it.

    A long run begun first and a short one begun after it finish out of order,
    and `max(id)` then reports the earlier verdict as current. This is the case
    the query was written against, so it is the case that has to be in the file.
    """
    now = datetime.now(timezone.utc)

    # id 1: started first, finished last.
    runs.add(IngesterRun(
        source="openaq", started_at=now - timedelta(minutes=30),
        finished_at=now - timedelta(minutes=1),
        status="degraded", reason_code="source_empty",
        required_action="escalate", source_vintage_seconds=9 * 365 * 86400,
        records_received=0, records_accepted=0, records_rejected=0))
    runs.commit()

    # id 2: started second, finished first.
    runs.add(IngesterRun(
        source="openaq", started_at=now - timedelta(minutes=20),
        finished_at=now - timedelta(minutes=10),
        status="success", reason_code="ok",
        required_action="none", source_vintage_seconds=60,
        records_received=10, records_accepted=10, records_rejected=0))
    runs.commit()

    body = client.get("/api/v1/ingestion/attention").json()

    assert body["count"] == 1
    assert body["sources"][0]["required_action"] == "escalate", (
        "the run with the larger id finished earlier; its verdict is not the "
        "current one"
    )


def test_a_tie_on_finished_at_resolves_deterministically(client, runs):
    """Two runs finishing within the same clock tick still give one answer.

    Without the id tie-break the query returns both rows for the source, and
    the endpoint would report a source twice -- or pick whichever the database
    happened to hand back first.
    """
    now = datetime.now(timezone.utc)
    same = now - timedelta(minutes=5)

    for action in ("escalate", "none"):
        runs.add(IngesterRun(
            source="rosstat", started_at=now - timedelta(minutes=10),
            finished_at=same, status="degraded", reason_code="source_empty",
            required_action=action, source_vintage_seconds=1,
            records_received=0, records_accepted=0, records_rejected=0))
        runs.commit()

    body = client.get("/api/v1/ingestion/attention").json()

    assert body["count"] == 1, f"{body['count']} rows for one source"
    # The larger id breaks the tie, and it is the one added last.
    assert body["sources"][0]["required_action"] == "none"


def test_a_none_without_a_declared_tolerance_says_so(client, runs):
    """`none` must not read as proven freshness.

    A clean run of a source nobody has given a `max_vintage_hours` is healthy
    as a run, and says nothing about whether the data is current. Without the
    status on the row, an operator sees `none` and infers the second.
    """
    _add(runs, "rosstat", "none", reason="ok", status="success",
         age=590 * 86400)
    runs.query(IngesterRun).filter(IngesterRun.source == "rosstat").update(
        {"freshness_status": "not_configured", "max_vintage_seconds": None})
    runs.commit()

    row = client.get("/api/v1/ingestion/attention").json()["sources"][0]

    assert row["required_action"] == "none"
    assert row["freshness_status"] == "not_configured"
    assert row["max_vintage_seconds"] is None
    assert row["source_vintage_seconds"] == pytest.approx(590 * 86400)


def test_the_threshold_in_force_is_recorded_beside_the_verdict(client, runs):
    """A tolerance can be changed after the run.

    Without it, a row holding `escalate` and 590 days cannot be re-read: nobody
    can tell whether the data was old or the threshold moved.
    """
    _add(runs, "openaq", "escalate", reason="source_empty", age=9 * 365 * 86400)
    runs.query(IngesterRun).filter(IngesterRun.source == "openaq").update(
        {"freshness_status": "stale", "max_vintage_seconds": 30 * 86400.0})
    runs.commit()

    row = client.get("/api/v1/ingestion/attention").json()["sources"][0]

    assert row["freshness_status"] == "stale"
    assert row["max_vintage_seconds"] == pytest.approx(30 * 86400)
