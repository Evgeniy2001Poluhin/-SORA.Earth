"""Tests for forecasting base classes."""

import pytest
import pandas as pd
from datetime import datetime

from app.services.forecasting.base import BaseForecastModel, ForecastResult


def test_forecast_result_dataclass():
    """Test ForecastResult dataclass creation and attributes."""
    result = ForecastResult(
        dates=["2026-07-10", "2026-07-11"],
        yhat=[75.0, 76.0],
        yhat_lower=[70.0, 71.0],
        yhat_upper=[80.0, 81.0],
        model_name="TestModel",
        model_version="1.0",
        confidence="high",
        metadata={"test": "data"}
    )

    assert len(result.dates) == 2
    assert result.dates[0] == "2026-07-10"
    assert result.yhat[0] == 75.0
    assert result.yhat_lower[0] == 70.0
    assert result.yhat_upper[0] == 80.0
    assert result.model_name == "TestModel"
    assert result.model_version == "1.0"
    assert result.confidence == "high"
    assert result.metadata["test"] == "data"


def test_forecast_result_confidence_levels():
    """Test different confidence level assignments."""
    for conf in ["high", "medium", "low"]:
        result = ForecastResult(
            dates=["2026-07-10"],
            yhat=[75.0],
            yhat_lower=[70.0],
            yhat_upper=[80.0],
            model_name="Test",
            model_version="1.0",
            confidence=conf,
            metadata={}
        )
        assert result.confidence == conf


def test_base_forecast_model_abstract():
    """Test that BaseForecastModel cannot be instantiated directly."""
    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        BaseForecastModel("test", "1.0")


def test_base_forecast_model_inheritance():
    """Test that concrete implementations must override abstract methods."""

    class IncompleteForecast(BaseForecastModel):
        # Missing fit, predict, validate implementations
        pass

    with pytest.raises(TypeError):
        IncompleteForecast("test", "1.0")


def test_base_forecast_model_concrete_implementation():
    """Test that properly implemented models can be instantiated."""

    class ConcreteForecast(BaseForecastModel):
        def fit(self, df, target_col):
            pass

        def predict(self, horizon):
            return ForecastResult(
                dates=["2026-07-10"],
                yhat=[75.0],
                yhat_lower=[70.0],
                yhat_upper=[80.0],
                model_name=self.model_name,
                model_version=self.version,
                confidence="medium",
                metadata={}
            )

        def validate(self, df, target_col):
            return {"mae": 5.0, "rmse": 7.0, "mape": 8.5}

    model = ConcreteForecast("TestModel", "1.0")
    assert model.model_name == "TestModel"
    assert model.version == "1.0"

    result = model.predict(horizon=1)
    assert result.model_name == "TestModel"

    metrics = model.validate(pd.DataFrame(), "y")
    assert metrics["mae"] == 5.0
