"""GET /api/v1/lstm-status fits the ensemble once per change in its series.

Every call used to fit a full EnsembleForecaster: several seconds on the event
loop once the LSTM trains, every 30 seconds from the scheduler and once more for
each open Drift tab. The fit's outcome depends only on the series, so it is now
remembered against a fingerprint of that series and reused while the series is
unchanged. These tests pin four things: the fit runs once per data change; a
change to any value the fit reads runs it again; a failed fit is not retried
until the data changes; and the row-count facts and gauges are still read on
every call.

The fit is counted with a stand-in for EnsembleForecaster. The route imports that
name inside the function, so the name it reads is the module attribute looked up
at call time; patching the module attribute is the patch that reaches it.
"""
from __future__ import annotations

import datetime
import importlib
import inspect
import threading
import time

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

forecast_module = importlib.import_module("app.api.forecast")
ensemble_module = importlib.import_module("app.services.forecasting.ensemble")
prom_metrics = importlib.import_module("app.prom_metrics")

URL = "/api/v1/lstm-status"
WITH_LSTM = (("lstm", "prophet", "linear"), {"lstm": 0.7, "prophet": 0.3, "linear": 0.0})


@pytest.fixture(autouse=True)
def _forget_the_last_fit():
    """The endpoint remembers its last fit per process. No test may start with
    another test's outcome in place."""
    forecast_module._lstm_fit_cache.clear()
    yield
    forecast_module._lstm_fit_cache.clear()


class FakeDb:
    """Stands in for the session. `fetchone()` is the aggregate row (unique_days, last_date)."""

    def __init__(self, unique_days=5, last_date=datetime.date(2026, 9, 1)):
        self.unique_days = unique_days
        self.last_date = last_date

    def execute(self, *_a, **_k):
        return self

    def fetchone(self):
        return (self.unique_days, self.last_date)


def make_series(rows, *, start="2026-01-01", observed=None):
    dates = pd.date_range(start, periods=rows, freq="D")
    frame = pd.DataFrame({"ds": dates, "y": [1.0 + 0.1 * i for i in range(rows)]})
    if observed is not None:
        frame["is_observed"] = observed
    return frame


@pytest.fixture()
def db() -> FakeDb:
    return FakeDb()


@pytest.fixture()
def client(db) -> TestClient:
    app = FastAPI()
    app.include_router(forecast_module.router, prefix="/api/v1")
    app.dependency_overrides[forecast_module._get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def series(monkeypatch):
    """Set the frame the endpoint's series query returns. Each call gets a copy,
    as a real query would, so the cache has to recognise equal values rather than
    the same object."""
    state = {"frame": None}

    def install(frame):
        state["frame"] = frame

    monkeypatch.setattr(
        forecast_module, "_query_time_series",
        lambda _db, _metric: state["frame"].copy(),
    )
    return install


class FakeFits:
    def __init__(self):
        self.calls = 0
        self.fail = False
        self.delay = 0.0
        self.outcome = WITH_LSTM


@pytest.fixture()
def fits(monkeypatch) -> FakeFits:
    state = FakeFits()

    class CountingEnsemble:
        def __init__(self):
            self.weights = {}
            self._active_models = []

        def fit(self, df, target_col, **kwargs):
            state.calls += 1
            if state.delay:
                time.sleep(state.delay)
            if state.fail:
                raise RuntimeError("ensemble fit exploded")
            names, weights = state.outcome
            self._active_models = [(name, None) for name in names]
            self.weights = dict(weights)

    monkeypatch.setattr(ensemble_module, "EnsembleForecaster", CountingEnsemble)
    return state


# --- once per series ----------------------------------------------------------


def test_the_same_series_is_fitted_once(client, series, fits):
    series(make_series(40))
    first = client.get(URL).json()

    series(make_series(40))  # a new frame with equal values
    second = client.get(URL).json()

    assert fits.calls == 1
    assert second == first


def test_the_real_ensemble_is_fitted_once_for_an_unchanged_series(client, series, monkeypatch):
    """The same check without the stand-in, so the cache is shown against the fit
    production runs. Three rows keeps the real fit short."""
    calls = []
    real_fit = ensemble_module.EnsembleForecaster.fit

    def counting_fit(self, df, target_col, **kwargs):
        calls.append(len(df))
        return real_fit(self, df, target_col, **kwargs)

    monkeypatch.setattr(ensemble_module.EnsembleForecaster, "fit", counting_fit)
    series(make_series(3))

    first = client.get(URL).json()
    second = client.get(URL).json()

    assert len(calls) == 1
    assert first["reason_code"] is None
    assert second == first


# --- a change in the series refits --------------------------------------------


def test_a_changed_value_refits(client, series, fits):
    series(make_series(40))
    client.get(URL)

    changed = make_series(40)
    changed.loc[20, "y"] = 99.0  # one value; same length, same endpoints
    series(changed)
    client.get(URL)

    assert fits.calls == 2


def test_a_changed_date_in_the_middle_refits(client, series, fits):
    series(make_series(40))
    client.get(URL)

    changed = make_series(40)
    changed.loc[10, "ds"] = pd.Timestamp("2026-01-11 12:00")  # same length, endpoints and values
    series(changed)
    client.get(URL)

    assert fits.calls == 2


def test_an_added_row_refits(client, series, fits):
    series(make_series(40))
    client.get(URL)

    series(make_series(41))
    client.get(URL)

    assert fits.calls == 2


def test_a_changed_observed_flag_refits(client, series, fits):
    """The fit counts observed rows, so the flag is part of the series it reads."""
    series(make_series(40, observed=[True] * 40))
    client.get(URL)

    series(make_series(40, observed=[True] * 39 + [False]))
    client.get(URL)

    assert fits.calls == 2


# --- a failed fit is remembered for its series ---------------------------------


def test_a_failed_fit_is_not_retried_for_the_same_series(client, series, fits):
    fits.fail = True
    series(make_series(40))

    responses = [client.get(URL), client.get(URL)]

    assert fits.calls == 1, "a deterministic failure must not be refitted on every poll"
    for r in responses:
        assert r.status_code == 200
        assert r.json()["reason_code"] == "ensemble_fit_failed"
        assert r.json()["weights"] == {}


def test_a_failed_fit_is_retried_once_the_data_changes(client, series, fits):
    fits.fail = True
    series(make_series(40))
    assert client.get(URL).json()["reason_code"] == "ensemble_fit_failed"

    fits.fail = False
    series(make_series(41))
    body = client.get(URL).json()

    assert fits.calls == 2
    assert body["reason_code"] is None
    assert body["lstm_in_ensemble"] is True


# --- what is still read on every call -----------------------------------------


def test_the_row_count_facts_follow_the_database_on_a_cache_hit(client, db, series, fits):
    """The aggregate query is not part of the fitted series, so its facts change
    even while the fit is cached."""
    series(make_series(40))
    before = client.get(URL).json()

    db.unique_days = 9
    db.last_date = datetime.date(2026, 9, 9)
    after = client.get(URL).json()

    assert fits.calls == 1, "same series, so the fit stays cached"
    assert before["unique_days_raw"] == 5
    assert after["unique_days_raw"] == 9
    assert after["last_evaluation_date"] == "2026-09-09"


def test_the_gauges_are_set_on_every_call_even_when_the_fit_is_cached(client, series, fits):
    series(make_series(40))
    client.get(URL)
    prom_metrics.sora_forecast_samples_total.set(0)
    prom_metrics.sora_forecast_lstm_active.set(0)

    client.get(URL)

    assert fits.calls == 1
    assert prom_metrics.sora_forecast_samples_total._value.get() == 40
    assert prom_metrics.sora_forecast_lstm_active._value.get() == 1.0


def test_a_caller_cannot_edit_the_remembered_outcome(db, series, fits):
    series(make_series(40))
    first = forecast_module.get_lstm_status(db=db)
    first.weights["lstm"] = 0.0
    first.models_active.append("Nonsense")

    second = forecast_module.get_lstm_status(db=db)

    assert fits.calls == 1
    assert second.weights == dict(WITH_LSTM[1])
    assert "Nonsense" not in second.models_active


# --- the handler and the lock --------------------------------------------------


def test_the_handler_is_a_plain_function_so_fastapi_runs_it_in_the_threadpool():
    """`async def` ran the fit on the event loop and blocked the worker for its
    whole length. A regression to it must fail here, not show up as a stall."""
    route = next(r for r in forecast_module.router.routes if getattr(r, "path", "") == "/lstm-status")

    assert not inspect.iscoroutinefunction(forecast_module.get_lstm_status)
    assert not inspect.iscoroutinefunction(route.endpoint)


def test_concurrent_requests_for_a_new_series_fit_it_once(db, series, fits):
    """The lock is held across the fit, so four requests that arrive together for
    a series nobody has fitted yet produce one fit, and all four get its answer."""
    fits.delay = 0.2
    series(make_series(40))
    results, errors = [], []
    together = threading.Barrier(4)

    def call():
        try:
            together.wait()
            results.append(forecast_module.get_lstm_status(db=db))
        except Exception as exc:  # reported by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=call) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    assert fits.calls == 1
    assert len(results) == 4
    assert all(r.reason_code is None for r in results)
    assert len({tuple(sorted(r.weights.items())) for r in results}) == 1
