"""Both branches of GET /api/v1/lstm-status (docs/API_CONTRACT_ROADMAP.md).

The failure branch answered 200 with `active: false`. "We could not determine
whether LSTM is active" and "LSTM is not active" are different answers, and the
screen showed the second for both -- the same defect as `drift_detected: false`
in #239, on a different page.

Two quieter faults came with it. `samples` meant rows in the forecast series on
one branch and distinct days on the other: one name, two quantities. And
`days_remaining: 0` reads as "ready today" when nothing was measured.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

forecast_module = importlib.import_module("app.api.forecast")

KEYS = {
    "status", "active", "samples", "threshold", "days_remaining",
    "next_activation_date", "models_active", "weights", "unique_days_raw",
    "last_evaluation_date", "message", "reason_code",
}


class FakeResult:
    """What `fetchone()` on the aggregate query returns."""

    def __init__(self, unique_days, last_date):
        self._v = (unique_days, last_date)

    def __getitem__(self, i):
        return self._v[i]


class FakeDb:
    def __init__(self, unique_days=5, last_date=None):
        import datetime

        self._row = FakeResult(unique_days, last_date or datetime.date(2026, 9, 1))

    def execute(self, *_a, **_k):
        return self

    def fetchone(self):
        return self._row


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    app = FastAPI()
    app.include_router(forecast_module.router, prefix="/api/v1")
    app.dependency_overrides[forecast_module._get_db] = lambda: FakeDb()
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def series(monkeypatch):
    """Replace the forecast series query; it is not what this contract is about."""

    def install(rows: int):
        import pandas as pd

        monkeypatch.setattr(
            forecast_module,
            "_query_time_series",
            lambda db, col: pd.DataFrame({"ds": range(rows), "y": [1.0] * rows}),
        )

    return install


def test_a_determined_status_says_so_with_a_boolean(client, series):
    series(3)

    r = client.get("/api/v1/lstm-status")

    assert r.status_code == 200
    body = r.json()
    assert set(body) == KEYS
    assert body["status"] == "ok"
    assert isinstance(body["active"], bool)
    assert isinstance(body["days_remaining"], int)
    assert body["reason_code"] is None


def test_samples_counts_the_series_not_the_days(client, series):
    """One name, one quantity. The failure branch used to put a day count here."""
    series(7)

    body = client.get("/api/v1/lstm-status").json()

    assert body["samples"] == 7, "rows in the forecast series"
    assert body["unique_days_raw"] == 5, "distinct days, under its own name"


def test_a_failed_check_is_not_a_verdict_that_lstm_is_inactive(client, monkeypatch):
    """The defect this file exists for."""
    def explode(*_a, **_k):
        raise RuntimeError("series query failed")

    monkeypatch.setattr(forecast_module, "_query_time_series", explode)

    r = client.get("/api/v1/lstm-status")

    assert r.status_code == 503, "it used to be 200"
    body = r.json()
    assert set(body) == KEYS
    assert body["status"] == "unavailable"
    assert body["active"] is None, "false would claim LSTM was checked and found off"
    assert body["days_remaining"] is None, "zero would read as 'ready today'"
    assert body["next_activation_date"] is None
    assert body["reason_code"] == "status_check_failed"


def test_the_failed_check_still_reports_what_it_does_know(client, monkeypatch):
    monkeypatch.setattr(
        forecast_module, "_query_time_series",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("nope")),
    )

    body = client.get("/api/v1/lstm-status").json()

    assert body["unique_days_raw"] == 5, "the first query succeeded"
    assert body["samples"] == 0, "and this field is not repurposed to carry it"
    assert body["threshold"] > 0


def test_the_exception_text_is_not_returned(client, monkeypatch):
    secret = "postgresql://sora:hunter2@db:5432/sora"
    monkeypatch.setattr(
        forecast_module, "_query_time_series",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError(f"cannot reach {secret}")),
    )

    text = client.get("/api/v1/lstm-status").text

    assert "hunter2" not in text
    assert "error" not in client.get("/api/v1/lstm-status").json()


def test_openapi_declares_both_branches_and_pins_active(client):
    schema = client.get("/openapi.json").json()
    op = schema["paths"]["/api/v1/lstm-status"]["get"]

    assert "503" in op["responses"]

    components = schema["components"]["schemas"]
    for model in ("LstmStatusOk", "LstmStatusUnavailable"):
        assert KEYS <= set(components[model]["properties"]), model

    assert components["LstmStatusOk"]["properties"]["active"].get("type") == "boolean"
    assert components["LstmStatusUnavailable"]["properties"]["active"].get("type") == "null"
