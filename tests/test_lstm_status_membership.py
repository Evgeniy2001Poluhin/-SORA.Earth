"""Observation eligibility and actual fitted membership are separate facts.

Synthetic time series only; all ensemble fitting is replaced with a stand-in.
"""
import math
from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api import forecast
from app import prom_metrics
from app.services.forecasting import ensemble as ensemble_module


class FakeDb:
    def execute(self, *_a, **_kw):
        return self

    def fetchone(self):
        return (10, date(2026, 10, 16))


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(forecast.router, prefix="/api/v1")
    app.dependency_overrides[forecast._get_db] = FakeDb
    forecast._lstm_fit_cache.clear()
    yield TestClient(app)
    forecast._lstm_fit_cache.clear()
    prom_metrics.sora_forecast_lstm_active.set(float("nan"))


def answer(client, observed=10, member=False, failed=False):
    frame = pd.DataFrame({"ds": pd.date_range("2026-01-01", periods=40),
                          "y": [float(i) for i in range(40)],
                          "is_observed": [True] * observed + [False] * (40-observed)})

    class FakeEnsemble:
        def fit(self, _frame, _target):
            if failed:
                raise RuntimeError("synthetic fit failure")
            self._active_models = [("lstm", None)] if member else [("prophet", None)]
            self.weights = {"lstm": 1.0} if member else {"prophet": 1.0}

    with patch.object(forecast, "_query_time_series", return_value=frame), \
         patch.object(ensemble_module, "EnsembleForecaster", FakeEnsemble):
        response = client.get("/api/v1/lstm-status")
    assert response.status_code == 200
    return response.json()


@pytest.mark.parametrize("observed,eligible,remaining", [(10, False, 23), (33, True, 0), (40, True, 0)])
def test_eligibility_counts_observations_not_interpolated_rows(client, observed, eligible, remaining):
    body = answer(client, observed=observed)
    assert body["samples"] == 40  # legacy dense-row field stays compatible
    assert body["active"] is True  # legacy dense-row eligibility, NOT membership
    assert body["observed_samples"] == observed
    assert body["eligible_for_training"] is eligible
    assert body["observations_remaining"] == remaining
    assert body["lstm_in_ensemble"] is False
    assert "LSTM active" not in body["message"]


def test_successful_fit_without_lstm_does_not_claim_it_is_active(client):
    body = answer(client, observed=40)
    assert body["lstm_in_ensemble"] is False
    assert "not in the fitted ensemble" in body["message"]
    assert prom_metrics.sora_forecast_lstm_active._value.get() == 0


def test_failed_fit_is_unknown_including_the_metric(client):
    body = answer(client, observed=40, failed=True)
    assert body["eligible_for_training"] is True
    assert body["lstm_in_ensemble"] is None
    assert body["reason_code"] == "ensemble_fit_failed"
    assert math.isnan(prom_metrics.sora_forecast_lstm_active._value.get())


def test_actual_lstm_membership_is_active(client):
    body = answer(client, observed=40, member=True)
    assert body["lstm_in_ensemble"] is True
    assert body["message"] == "LSTM is in the fitted ensemble."
    assert prom_metrics.sora_forecast_lstm_active._value.get() == 1


def test_query_failure_clears_previous_active_metric(client):
    answer(client, observed=40, member=True)
    with patch.object(forecast, "_query_time_series", side_effect=RuntimeError("synthetic query failure")):
        response = client.get("/api/v1/lstm-status")
    assert response.status_code == 503
    assert math.isnan(prom_metrics.sora_forecast_lstm_active._value.get())


def test_empty_series_reports_zero_observations_not_a_fitted_model(client):
    with patch.object(forecast, "_query_time_series", return_value=pd.DataFrame()), \
         patch.object(forecast, "_lstm_fit_outcome") as fit:
        response = client.get("/api/v1/lstm-status")
    fit.assert_not_called()
    assert response.status_code == 200
    body = response.json()
    assert body["observed_samples"] == 0
    assert body["eligible_for_training"] is False
    assert body["observations_remaining"] == ensemble_module.LSTM_MIN_ROWS
    assert body["lstm_in_ensemble"] is None
    assert math.isnan(prom_metrics.sora_forecast_lstm_active._value.get())


@pytest.mark.parametrize("observed", [10, 32, 33, 40])
def test_status_count_matches_real_ensemble_gate_without_training(observed):
    frame = pd.DataFrame({"ds": pd.date_range("2026-01-01", periods=40),
                          "y": [float(i) for i in range(40)],
                          "is_observed": [True] * observed + [False] * (40-observed)})
    from unittest.mock import Mock
    model = ensemble_module.EnsembleForecaster()
    model.lstm.fit = Mock()
    model.prophet.fit = Mock()
    model.linear.fit = Mock()
    model.fit(frame, "y")
    assert ensemble_module.observed_sample_count(frame) == observed
    assert model.lstm.fit.called is (observed >= ensemble_module.LSTM_MIN_ROWS)
    assert ("lstm" in dict(model._active_models)) is (observed >= ensemble_module.LSTM_MIN_ROWS)


def test_legacy_frame_without_provenance_retains_row_count():
    assert ensemble_module.observed_sample_count(pd.DataFrame({"y": [1, 2, 3]})) == 3
