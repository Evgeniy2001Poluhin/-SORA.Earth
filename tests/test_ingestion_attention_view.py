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
        source_age_seconds=age, records_received=0, records_accepted=0,
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

    for field in ("reason_code", "source_age_seconds", "records_received",
                  "records_accepted", "records_rejected", "status",
                  "finished_at"):
        assert field in row, field
    assert row["reason_code"] == "source_empty"
    assert row["source_age_seconds"] == pytest.approx(9 * 365 * 86400)
