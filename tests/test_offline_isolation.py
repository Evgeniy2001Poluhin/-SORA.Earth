"""Regression tests for the offline / scheduler boundary.

CI runs the suite with SORA_OFFLINE=1 and RUN_SCHEDULER=false. Both were
conventions rather than enforced boundaries: a test that assigned RUN_SCHEDULER
directly started the real BackgroundScheduler, whose ingestion jobs then reached
api.open-meteo.com from inside the runner. These tests pin the boundary.
"""
import os
import socket

import pytest


OFFLINE = os.getenv("SORA_OFFLINE", "0") == "1"

pytestmark = pytest.mark.skipif(
    not OFFLINE, reason="offline boundary only applies when SORA_OFFLINE=1"
)


def test_external_connection_is_blocked():
    """An outbound connection must fail fast rather than hang or succeed."""
    with pytest.raises(RuntimeError, match="Blocked external connection"):
        socket.create_connection(("api.open-meteo.com", 443), timeout=1)


def test_loopback_stays_open():
    """Blocking must not break the TestClient or local sqlite."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        conn = socket.create_connection(server.getsockname(), timeout=1)
        conn.close()
    finally:
        server.close()


def test_run_scheduler_false_prevents_startup(monkeypatch):
    """RUN_SCHEDULER=false must actually stop init_scheduler from starting."""
    from app.scheduler import init_scheduler, scheduler

    monkeypatch.setenv("RUN_SCHEDULER", "false")
    assert not scheduler.running, "a previous test left the scheduler running"

    init_scheduler()

    assert not scheduler.running
    assert scheduler.get_jobs() == []


def test_world_bank_fetch_is_guarded():
    """external_data must short-circuit instead of reaching the network."""
    from app import external_data

    # Returns the offline fallback rather than raising the blocked-connection error.
    result = external_data._fetch_wb_indicator("SWE", "EN.ATM.CO2E.PC")
    assert result is None or isinstance(result, (int, float))


def test_mlflow_stats_does_not_dial_the_tracking_server():
    """get_experiment_stats used to retry against 127.0.0.1:5556 for minutes."""
    from app.mlflow_tracking import get_experiment_stats

    stats = get_experiment_stats()
    assert stats["total_runs"] == 0
    assert "experiment" in stats
