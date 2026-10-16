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
ensemble_module = importlib.import_module("app.services.forecasting.ensemble")
LSTM_MIN_ROWS = ensemble_module.LSTM_MIN_ROWS

KEYS = {
    "status", "active", "samples", "threshold", "days_remaining",
    "next_activation_date", "models_active", "weights", "unique_days_raw",
    "last_evaluation_date", "message", "reason_code",
}
# The 200 body only. On the 503 branch nothing was fitted, so there is no
# `lstm_in_ensemble` to report, and it is absent rather than null.
OK_KEYS = KEYS | {"lstm_in_ensemble"}


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
    """Replace the forecast series query; it is not what this contract is about.

    `ds` must be dates, as `load_time_series` returns them. Integer `ds` made
    every ensemble fit raise, so these tests used to take the fit-failure branch
    while their names said they were testing a determined status.
    """

    def install(rows: int):
        import pandas as pd

        dates = pd.date_range("2026-01-01", periods=rows, freq="D")
        monkeypatch.setattr(
            forecast_module,
            "_query_time_series",
            lambda db, col: pd.DataFrame({"ds": dates, "y": [1.0] * rows}),
        )

    return install


def test_a_determined_status_says_so_with_a_boolean(client, series):
    series(3)

    r = client.get("/api/v1/lstm-status")

    assert r.status_code == 200
    body = r.json()
    assert set(body) == OK_KEYS
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


# --- The ensemble fit ---------------------------------------------------------
#
# `weights`, `models_active` and `lstm_in_ensemble` describe the fitted ensemble,
# so they are measured from the fit. `active` is eligibility by row count, and
# must not depend on the fit at all. The fit is replaced here, not run: these
# tests are about what the endpoint reports for each outcome, not about training.

FIT_FAILS = None
# (models the fit reports as active, the weights it reports)
WITH_LSTM = (("lstm", "prophet", "linear"), {"lstm": 0.7, "prophet": 0.3, "linear": 0.0})
WITHOUT_LSTM = (("prophet", "linear"), {"lstm": 0.0, "prophet": 0.6, "linear": 0.4})


@pytest.fixture()
def fit(monkeypatch):
    """Make the endpoint's ensemble fit raise, or report the given outcome."""

    def install(outcome):
        class FakeEnsemble:
            def __init__(self):
                self.weights = {"lstm": 0.0, "prophet": 0.0, "linear": 0.0}
                self._active_models = []

            def fit(self, df, target_col, **kwargs):
                if outcome is None:
                    raise RuntimeError("ensemble fit exploded")
                names, weights = outcome
                self._active_models = [(name, None) for name in names]
                self.weights = dict(weights)

        monkeypatch.setattr(ensemble_module, "EnsembleForecaster", FakeEnsemble)

    return install


def _every_number(value):
    """Each number anywhere in a decoded JSON value. Booleans are not numbers here."""
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _every_number(v)
    elif isinstance(value, list):
        for v in value:
            yield from _every_number(v)


def test_a_failed_fit_reports_no_weights_and_no_models(client, series, fit):
    """The fallback used to send lstm 0.0, prophet 0.9, linear 0.1 and the
    models Prophet and LinearTrend, as if they had been measured. None of that
    was measured, so none of it is reported."""
    series(40)
    fit(FIT_FAILS)

    r = client.get("/api/v1/lstm-status")

    assert r.status_code == 200
    body = r.json()
    assert set(body) == OK_KEYS
    assert body["status"] == "ok"
    assert body["weights"] == {}
    assert body["models_active"] == []
    assert body["lstm_in_ensemble"] is None
    assert body["reason_code"] == "ensemble_fit_failed"
    assert "could not be fitted" in body["message"]
    numbers = list(_every_number(body))
    assert 0.9 not in numbers and 0.1 not in numbers, "the invented weights"


def test_lstm_in_ensemble_is_true_when_the_fitted_ensemble_includes_it(client, series, fit):
    series(40)
    fit(WITH_LSTM)

    body = client.get("/api/v1/lstm-status").json()

    assert body["lstm_in_ensemble"] is True
    assert body["reason_code"] is None
    assert [m.lower() for m in body["models_active"]] == ["lstm", "prophet", "linear"]
    assert body["weights"] == WITH_LSTM[1]


def test_eligible_by_row_count_is_not_the_same_as_in_the_ensemble(client, series, fit):
    """The distinction this contract exists for: the series is long enough to be
    eligible, and the fitted ensemble still leaves the LSTM out."""
    series(40)
    fit(WITHOUT_LSTM)

    body = client.get("/api/v1/lstm-status").json()

    assert body["active"] is True, "eligible by row count"
    assert body["lstm_in_ensemble"] is False, "and not in the fitted ensemble"
    assert body["reason_code"] is None


@pytest.mark.parametrize(
    "rows, outcome",
    [
        (LSTM_MIN_ROWS - 1, FIT_FAILS),
        (LSTM_MIN_ROWS - 1, WITHOUT_LSTM),
        (LSTM_MIN_ROWS, FIT_FAILS),
        (LSTM_MIN_ROWS, WITHOUT_LSTM),
        (LSTM_MIN_ROWS, WITH_LSTM),
    ],
    # No "below threshold, with LSTM" row: the real ensemble never fits the LSTM
    # below LSTM_MIN_ROWS, so that combination cannot occur.
    ids=["below-fit-fails", "below-no-lstm", "at-fit-fails", "at-no-lstm", "at-with-lstm"],
)
def test_active_follows_the_row_count_whatever_the_fit_did(client, series, fit, rows, outcome):
    series(rows)
    fit(outcome)

    body = client.get("/api/v1/lstm-status").json()

    assert body["status"] == "ok"
    assert body["active"] == (rows >= LSTM_MIN_ROWS)
    assert body["samples"] == rows
    assert body["days_remaining"] == (0 if rows >= LSTM_MIN_ROWS else LSTM_MIN_ROWS - rows)
    if outcome is None:
        assert body["lstm_in_ensemble"] is None
        assert body["reason_code"] == "ensemble_fit_failed"
    else:
        assert body["lstm_in_ensemble"] == ("lstm" in outcome[0])
        assert body["reason_code"] is None


def test_openapi_declares_both_branches_and_pins_active(client):
    schema = client.get("/openapi.json").json()
    op = schema["paths"]["/api/v1/lstm-status"]["get"]

    assert "503" in op["responses"]

    components = schema["components"]["schemas"]
    for model in ("LstmStatusOk", "LstmStatusUnavailable"):
        assert KEYS <= set(components[model]["properties"]), model

    assert "lstm_in_ensemble" in components["LstmStatusOk"]["properties"]
    assert components["LstmStatusOk"]["properties"]["active"].get("type") == "boolean"
    assert components["LstmStatusUnavailable"]["properties"]["active"].get("type") == "null"
