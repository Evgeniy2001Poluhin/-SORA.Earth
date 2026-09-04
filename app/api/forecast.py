"""Time series forecasting API with model caching and async execution."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from functools import partial

import numpy as np
import pandas as pd
import logging
from fastapi import APIRouter, Query, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.schemas import ForecastResponse, ForecastPoint, HistoryPoint, ForecastCacheStats
from app.services.forecasting import ModelRegistry, forecast_cache

log = logging.getLogger(__name__)
router = APIRouter(tags=["forecast"])

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="forecast")


def _get_db():
    from app.main import get_db_sync
    db = get_db_sync()
    try:
        yield db
    finally:
        db.close()


def _query_time_series(db: Session, metric: str) -> pd.DataFrame:
    """Delegate to the shared time-series loader.

    What the returned frame contains, and the limits on reading it, are
    documented on `load_time_series`. This wrapper describes none of that on
    purpose: a second copy of another function's contract is exactly what went
    stale here, and it stayed wrong long after the behaviour it named was gone.
    """
    from app.services.forecasting.data_loader import load_time_series
    return load_time_series(db, metric)


def _fit_and_predict(model_name: str, metric: str, df: pd.DataFrame, horizon: int, country: str = None):
    """Fit model (with cache check) and predict. Runs in thread pool."""
    # Check cache first
    cached = forecast_cache.get_fitted_model(model_name, metric, df)
    if cached:
        log.debug(f"Using cached {model_name} model")
        return cached.predict(horizon)

    # Cache miss — fit fresh model
    forecaster = ModelRegistry.get(model_name)
    if forecaster is None:
        raise ValueError(f"Unknown model: {model_name}")

    log.info(f"Fitting {model_name} on {len(df)} samples for metric={metric}")
    forecaster.fit(df, target_col="y", country=country)
    forecast_cache.store_fitted_model(model_name, metric, df, forecaster)
    log.info(f"{model_name} fit complete, predicting horizon={horizon}")
    return forecaster.predict(horizon)


@router.get("/forecast", response_model=ForecastResponse)
async def forecast(
    horizon: int = Query(30, ge=1, le=180, description="Days ahead to forecast"),
    metric: str = Query("score", pattern="^(score|prob|co2_reduction)$", description="Target metric"),
    model: str = Query("ensemble", pattern="^(ensemble|prophet|lstm|linear)$", description="Model to use"),
    country: str = Query(None, min_length=2, max_length=10, description="Country/region code for external regressors"),
    db: Session = Depends(_get_db)
):
    """Time series forecast with production-grade models.

    Fallback chain: Ensemble -> Prophet -> LinearTrend (never fails).
    Models are cached for 5 minutes to avoid retraining on repeated requests.
    """
    df = _query_time_series(db, metric)

    if df.empty:
        return ForecastResponse(history=[], forecast=[], model="linear-trend", metric=metric)

    history = [HistoryPoint(ds=str(d.date()), y=round(float(v), 3))
               for d, v in zip(df["ds"], df["y"])]

    if len(df) < 3:
        return ForecastResponse(history=history, forecast=[], model="linear-trend", metric=metric)

    # Try production models with async execution
    if model != "linear":
        fallback_chain = [model]
        if model != "prophet":
            fallback_chain.append("prophet")

        for m in fallback_chain:
            try:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    _executor, partial(_fit_and_predict, m, metric, df, horizon, country)
                )

                # Clip predictions to valid range based on metric type
                if metric == "score":
                    lo, hi = 0.0, 100.0
                    min_ci_width = 5.0  # Minimum CI width for score (±2.5 points)
                elif metric == "prob":
                    lo, hi = 0.0, 1.0
                    min_ci_width = 0.1  # Minimum CI width for probability (±5%)
                else:  # co2_reduction
                    lo, hi = 0.0, float('inf')
                    min_ci_width = 10.0  # Minimum CI width for CO2 (±5 tons)

                forecast_pts = []
                clipped_count = 0
                for d, y, yl, yu in zip(result.dates, result.yhat, result.yhat_lower, result.yhat_upper):
                    # Clip to valid range
                    y_clipped = max(lo, min(hi, y))
                    yl_clipped = max(lo, min(hi, yl))
                    yu_clipped = max(lo, min(hi, yu))

                    # Track if significant clipping occurred
                    if abs(y - y_clipped) > 0.01 or abs(yu - yu_clipped) > 0.01 or abs(yl - yl_clipped) > 0.01:
                        clipped_count += 1

                    # Ensure minimum CI width for interpretability
                    ci_width = yu_clipped - yl_clipped
                    if ci_width < min_ci_width:
                        # Expand interval symmetrically around point prediction
                        expand = (min_ci_width - ci_width) / 2
                        yl_clipped = max(lo, yl_clipped - expand)
                        yu_clipped = min(hi, yu_clipped + expand)

                    forecast_pts.append(
                        ForecastPoint(
                            ds=d,
                            yhat=round(y_clipped, 3),
                            yhat_lower=round(yl_clipped, 3),
                            yhat_upper=round(yu_clipped, 3)
                        )
                    )

                # Downgrade confidence if significant clipping occurred
                adjusted_confidence = result.confidence
                if clipped_count > len(forecast_pts) * 0.3:  # >30% of points clipped
                    adjusted_confidence = "low"
                    log.warning(f"Downgraded confidence to 'low': {clipped_count}/{len(forecast_pts)} points clipped beyond valid range")

                response = ForecastResponse(
                    history=history,
                    forecast=forecast_pts,
                    model=result.model_name,
                    metric=metric,
                    confidence=adjusted_confidence,
                    metadata=result.metadata
                )

                # Store forecast in history
                _store_forecast_history(
                    db=db,
                    metric=metric,
                    model=result.model_name,
                    horizon=horizon,
                    country=country,
                    forecast=forecast_pts,
                    confidence=result.confidence,
                    metadata=result.metadata
                )

                return response
            except Exception as e:
                log.warning(f"{m} failed: {e}, trying next in fallback chain", exc_info=True)

    # Fallback: Linear trend
    return _linear_forecast(df, horizon, metric, history)


@router.delete("/forecast/cache", response_model=ForecastCacheStats)
async def invalidate_forecast_cache(model_name: str = Query(None)):
    """Invalidate forecast model cache (all or specific model)."""
    invalidated = forecast_cache.invalidate(model_name)
    return ForecastCacheStats(cache_size=forecast_cache.size, invalidated=invalidated)


@router.get("/forecast/cache", response_model=ForecastCacheStats)
async def get_forecast_cache_stats():
    """Get forecast cache statistics."""
    return ForecastCacheStats(cache_size=forecast_cache.size)


@router.post("/forecast/pretrain")
async def trigger_pretrain():
    """Manually trigger forecast model pre-training (admin)."""
    loop = asyncio.get_event_loop()
    from app.scheduler import scheduled_pretrain_forecast_models
    result = await loop.run_in_executor(_executor, scheduled_pretrain_forecast_models)
    return result


def _linear_forecast(
    df: pd.DataFrame, horizon: int, metric: str, history: list[HistoryPoint]
) -> ForecastResponse:
    """Fallback linear trend forecaster."""
    t0 = df["ds"].min()
    x = (df["ds"] - t0).dt.days.to_numpy(dtype=float)
    y = df["y"].to_numpy(dtype=float)

    coeffs = np.polyfit(x, y, 1)
    fit = np.poly1d(coeffs)
    resid = y - fit(x)
    sigma = float(np.std(resid, ddof=2)) if len(y) > 2 else 0.0
    ci = 1.96 * sigma

    last_day = int(x.max())
    fut_x = np.arange(last_day + 1, last_day + 1 + horizon, dtype=float)
    yhat = fit(fut_x)

    lo, hi = (0.0, 100.0) if metric == "score" else (0.0, 1.0)

    forecast_pts = [
        ForecastPoint(
            ds=str((t0 + timedelta(days=int(fx))).date()),
            yhat=round(float(np.clip(v, lo, hi)), 3),
            yhat_lower=round(float(np.clip(v - ci, lo, hi)), 3),
            yhat_upper=round(float(np.clip(v + ci, lo, hi)), 3),
        )
        for fx, v in zip(fut_x, yhat)
    ]

    return ForecastResponse(
        history=history,
        forecast=forecast_pts,
        model="LinearTrend",
        metric=metric,
        confidence="low",
        metadata={"slope_per_day": round(float(coeffs[0]), 5), "ci95": round(ci, 3)}
    )


def _store_forecast_history(
    db: Session,
    metric: str,
    model: str,
    horizon: int,
    country: str,
    forecast: List[ForecastPoint],
    confidence: str,
    metadata: dict
):
    """Store forecast in database for historical tracking."""
    from app.database import ForecastHistory

    try:
        forecast_json = json.dumps([
            {"ds": pt.ds, "yhat": pt.yhat, "yhat_lower": pt.yhat_lower, "yhat_upper": pt.yhat_upper}
            for pt in forecast
        ])
        metadata_json = json.dumps(metadata) if metadata else None

        record = ForecastHistory(
            metric=metric,
            model=model,
            horizon=horizon,
            country=country,
            forecast_json=forecast_json,
            confidence=confidence,
            metadata_json=metadata_json
        )
        db.add(record)
        db.commit()
        log.info(f"Stored forecast history: metric={metric}, model={model}, horizon={horizon}")
    except Exception as e:
        log.error(f"Failed to store forecast history: {e}")
        db.rollback()


@router.get("/forecast/history")
async def get_forecast_history(
    metric: str = Query(None, description="Filter by metric (score, prob, co2_reduction)"),
    model: str = Query(None, description="Filter by model name"),
    limit: int = Query(50, ge=1, le=500, description="Max records to return"),
    db: Session = Depends(_get_db)
):
    """Get historical forecast records.

    Returns past forecasts with metadata for analysis and comparison.
    """
    from app.database import ForecastHistory

    query = db.query(ForecastHistory).order_by(ForecastHistory.created_at.desc())

    if metric:
        query = query.filter(ForecastHistory.metric == metric)
    if model:
        query = query.filter(ForecastHistory.model == model)

    records = query.limit(limit).all()

    return [
        {
            "id": r.id,
            "created_at": r.created_at.isoformat(),
            "metric": r.metric,
            "model": r.model,
            "horizon": r.horizon,
            "country": r.country,
            "forecast": json.loads(r.forecast_json),
            "confidence": r.confidence,
            "metadata": json.loads(r.metadata_json) if r.metadata_json else None
        }
        for r in records
    ]


@router.get("/forecast/metrics/performance")
def get_forecast_model_performance(
    metric_name: str = Query(None, description="Filter by metric name (score, prob, co2_reduction)"),
    model_type: str = Query(None, description="Filter by model type (ensemble, lstm, prophet, linear)"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of records"),
    db: Session = Depends(_get_db)
):
    """Get forecast model performance metrics history (MAE, RMSE, R², MAPE).

    Returns historical performance metrics logged during automated retraining.
    Useful for monitoring model degradation and training trends over time.

    **Example Response:**
    ```json
    {
        "total": 12,
        "metrics": [
            {
                "id": 1,
                "trained_at": "2026-07-12T18:00:00",
                "metric_name": "score",
                "model_type": "ensemble",
                "train_samples": 24,
                "test_samples": 6,
                "mae": 2.45,
                "rmse": 3.12,
                "mape": 3.2,
                "r2_score": 0.92,
                "training_duration_sec": 12.5,
                "trigger_source": "auto_scheduler",
                "status": "success"
            }
        ]
    }
    ```
    """
    from app.database import ForecastModelMetrics

    query = db.query(ForecastModelMetrics)

    if metric_name:
        query = query.filter(ForecastModelMetrics.metric_name == metric_name)

    if model_type:
        query = query.filter(ForecastModelMetrics.model_type == model_type)

    query = query.order_by(ForecastModelMetrics.trained_at.desc())

    total = query.count()
    records = query.limit(limit).all()

    return {
        "total": total,
        "metrics": [
            {
                "id": r.id,
                "trained_at": r.trained_at.isoformat(),
                "metric_name": r.metric_name,
                "model_type": r.model_type,
                "train_samples": r.train_samples,
                "test_samples": r.test_samples,
                "mae": r.mae,
                "rmse": r.rmse,
                "mape": r.mape,
                "r2_score": r.r2_score,
                "training_duration_sec": r.training_duration_sec,
                "trigger_source": r.trigger_source,
                "status": r.status,
                "error_message": r.error_message
            }
            for r in records
        ]
    }


@router.get("/forecast/metrics/latest")
def get_latest_forecast_metrics(
    db: Session = Depends(_get_db)
):
    """Get latest performance metrics for all forecast models.

    Returns the most recent MAE/RMSE/R² for each metric/model combination.
    Useful for dashboard displays and quick health checks.

    **Example Response:**
    ```json
    {
        "score": {
            "ensemble": {
                "trained_at": "2026-07-12T18:00:00",
                "mae": 2.45,
                "rmse": 3.12,
                "r2_score": 0.92,
                "train_samples": 24
            }
        },
        "prob": { ... },
        "co2_reduction": { ... }
    }
    ```
    """
    from app.database import ForecastModelMetrics
    from sqlalchemy import func

    # Get latest record for each metric_name + model_type combination
    subquery = (
        db.query(
            ForecastModelMetrics.metric_name,
            ForecastModelMetrics.model_type,
            func.max(ForecastModelMetrics.trained_at).label('max_trained_at')
        )
        .filter(ForecastModelMetrics.status == 'success')
        .group_by(ForecastModelMetrics.metric_name, ForecastModelMetrics.model_type)
        .subquery()
    )

    records = (
        db.query(ForecastModelMetrics)
        .join(
            subquery,
            (ForecastModelMetrics.metric_name == subquery.c.metric_name) &
            (ForecastModelMetrics.model_type == subquery.c.model_type) &
            (ForecastModelMetrics.trained_at == subquery.c.max_trained_at)
        )
        .all()
    )

    # Group by metric_name, then model_type
    result = {}
    for r in records:
        if r.metric_name not in result:
            result[r.metric_name] = {}

        result[r.metric_name][r.model_type] = {
            "trained_at": r.trained_at.isoformat(),
            "mae": r.mae,
            "rmse": r.rmse,
            "mape": r.mape,
            "r2_score": r.r2_score,
            "train_samples": r.train_samples,
            "test_samples": r.test_samples,
            "training_duration_sec": r.training_duration_sec
        }

    return result


@router.get("/lstm-status")
async def get_lstm_status(db: Session = Depends(_get_db)):
    """Get LSTM activation status and progress towards threshold.

    Returns:
        - active: bool - Whether LSTM is currently active in ensemble
        - samples: int - Current number of unique days in dataset
        - threshold: int - Required samples for LSTM activation
        - days_remaining: int - Days until threshold (if not active)
        - next_activation_date: str - Estimated date when LSTM will activate
        - models_active: list - Currently active models in ensemble
        - weights: dict - Current ensemble weights
    """
    from datetime import datetime, timedelta
    from app.services.forecasting.ensemble import LSTM_MIN_ROWS

    # Query current data volume
    from sqlalchemy import text
    result = db.execute(text("""
        SELECT
            COUNT(DISTINCT created_at::date) as unique_days,
            MAX(created_at)::date as last_date
        FROM evaluations
        WHERE created_at >= NOW() - INTERVAL '60 days'
    """)).fetchone()

    unique_days = result[0] if result else 0
    # `fetchone()` on an aggregate query always returns one row, even over zero
    # matching source rows -- `MAX(created_at)` is then NULL, not the row
    # itself. `if result else ...` never catches that (the row is truthy), so
    # an empty `evaluations` table (a fresh deploy, no data in the last 60
    # days) passed `last_date=None` through to `last_date + timedelta(...)`
    # below and crashed on every call. Checked on the value, not the row.
    last_date = result[1] if result and result[1] is not None else datetime.now().date()

    # Load data and check ensemble behavior
    try:
        df = _query_time_series(db, "score")
        n_samples = len(df)

        # Check if LSTM would activate with current data
        lstm_active = n_samples >= LSTM_MIN_ROWS

        # Calculate days remaining
        days_remaining = max(0, LSTM_MIN_ROWS - n_samples) if not lstm_active else 0

        # Estimate activation date (assuming 1 eval/day growth rate)
        next_activation_date = None
        if not lstm_active and days_remaining > 0:
            next_activation_date = (last_date + timedelta(days=days_remaining)).isoformat()

        # Get current ensemble weights by simulating fit
        from app.services.forecasting.ensemble import EnsembleForecaster
        ensemble = EnsembleForecaster()
        try:
            ensemble.fit(df, "y")
            weights = ensemble.weights
            models_active = [name.title() for name, model in ensemble._active_models]
        except Exception as e:
            log.warning(f"Could not fit ensemble for status check: {e}")
            weights = {"lstm": 0.0, "prophet": 0.9, "linear": 0.1}
            models_active = ["Prophet", "LinearTrend"]

        # Export metrics to Prometheus
        from app.prom_metrics import (
            sora_forecast_samples_total,
            sora_forecast_lstm_active,
            sora_forecast_days_remaining,
        )
        sora_forecast_samples_total.set(n_samples)
        sora_forecast_lstm_active.set(1.0 if lstm_active else 0.0)
        sora_forecast_days_remaining.set(days_remaining)

        return {
            "active": lstm_active,
            "samples": n_samples,
            "threshold": LSTM_MIN_ROWS,
            "days_remaining": days_remaining,
            "next_activation_date": next_activation_date,
            "models_active": models_active,
            "weights": weights,
            "unique_days_raw": unique_days,
            "last_evaluation_date": last_date.isoformat(),
            "message": "LSTM active" if lstm_active else f"Need {days_remaining} more days of data"
        }
    except Exception as e:
        log.error(f"LSTM status check failed: {e}", exc_info=True)
        return {
            "active": False,
            "samples": unique_days,
            "threshold": LSTM_MIN_ROWS,
            "days_remaining": max(0, LSTM_MIN_ROWS - unique_days),
            "error": str(e),
            "message": "Error checking LSTM status"
        }
