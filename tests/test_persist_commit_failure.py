"""A rolled-back batch must not report rows it does not have.

The counts used to be assigned before `db.commit()`. A commit failure rolled the
transaction back and left them in place, so the result carried `accepted > 0`
next to `persist_error:`. app/ingesters/runner.py writes that number into
ingester_runs.rows_written, which turns a transient failure into a permanent
false record of ingestion.
"""
from datetime import datetime, timezone

import pytest

import app.ingesters.persist as persist
from app.ingesters.base import Signal
from app.ingesters.persist import persist_environmental_observations


def signal(value=1.0):
    return Signal(
        region_code="RU-001",
        source="test_source",
        metric="pm25",
        value=value,
        unit="ug/m3",
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        metadata={},
    )


class ExplodingSession:
    """Accepts everything, then fails at the commit -- the case that mattered."""

    def __init__(self):
        self.rolled_back = False
        self.closed = False

    def commit(self):
        raise RuntimeError("the commit failed")

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


@pytest.fixture
def exploding(monkeypatch):
    session = ExplodingSession()
    monkeypatch.setattr(persist, "SessionLocal", lambda: session)
    monkeypatch.setattr(persist, "_upsert_observations",
                        lambda db, obs: (len(obs), 0, 0))
    return session


def test_a_failed_commit_reports_nothing_accepted(exploding):
    result = persist_environmental_observations([signal(), signal(2.0)],
                                                source="test_source")

    assert exploding.rolled_back, "the transaction was not rolled back"
    assert result.accepted == 0, f"accepted={result.accepted} after a rollback"
    assert result.inserted == 0
    assert result.updated == 0
    assert result.duplicates == 0


def test_the_failure_is_visible_in_the_result(exploding):
    """A consumer reading only counts must still be able to see the fault."""
    result = persist_environmental_observations([signal()], source="test_source")

    assert any("persist_error" in e for e in result.errors), \
        "a failed batch reported no error"


def test_the_session_is_closed_on_the_failure_path(exploding):
    persist_environmental_observations([signal()], source="test_source")
    assert exploding.closed


def test_a_successful_commit_does_report_the_counts(monkeypatch):
    """The counts must still arrive when the commit works -- the fix must not
    have simply stopped reporting."""
    class GoodSession(ExplodingSession):
        def commit(self):
            pass

    session = GoodSession()
    monkeypatch.setattr(persist, "SessionLocal", lambda: session)
    monkeypatch.setattr(persist, "_upsert_observations",
                        lambda db, obs: (len(obs), 0, 0))

    result = persist_environmental_observations([signal(), signal(2.0)],
                                                source="test_source")

    assert result.accepted == 2
    assert result.inserted == 2
    assert not [e for e in result.errors if "persist_error" in e]
