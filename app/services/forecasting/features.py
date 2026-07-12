"""Time series feature engineering for forecasting models."""

import pandas as pd
import numpy as np
from typing import List, Optional
import logging

log = logging.getLogger(__name__)


class FeatureEngineer:
    """Comprehensive feature engineering pipeline for time series forecasting.

    Features generated:
    - Lags: t-1, t-7, t-30
    - Rolling statistics: MA7, MA30, STD7, STD30
    - Seasonality: day_of_week, month, quarter, is_weekend
    - External regressors: GDP, air quality, policy indices (placeholders)
    """

    def __init__(self, lookback_window: int = 30):
        """Initialize feature engineer.

        Args:
            lookback_window: Number of historical periods for lags/rolling stats
        """
        self.lookback = lookback_window

    def add_lags(self, df: pd.DataFrame, target_col: str, lags: List[int] = None) -> pd.DataFrame:
        """Add lagged features (t-1, t-7, t-30).

        Args:
            df: Input DataFrame with target column
            target_col: Name of target column
            lags: List of lag periods (default: [1, 7, 30])

        Returns:
            DataFrame with added lag columns
        """
        if lags is None:
            lags = [1, 7, 30]

        df = df.copy()
        for lag in lags:
            df[f"{target_col}_lag{lag}"] = df[target_col].shift(lag)
        return df

    def add_rolling_stats(self, df: pd.DataFrame, target_col: str, windows: List[int] = None) -> pd.DataFrame:
        """Add rolling statistics (mean, std) over windows.

        Args:
            df: Input DataFrame
            target_col: Target column name
            windows: Window sizes (default: [7, 30])

        Returns:
            DataFrame with rolling mean and std columns
        """
        if windows is None:
            windows = [7, 30]

        df = df.copy()
        for w in windows:
            df[f"{target_col}_ma{w}"] = df[target_col].rolling(w, min_periods=1).mean()
            df[f"{target_col}_std{w}"] = df[target_col].rolling(w, min_periods=1).std()
        return df

    def add_seasonality(self, df: pd.DataFrame, date_col: str = "ds") -> pd.DataFrame:
        """Add temporal features (day_of_week, month, quarter, is_weekend).

        Args:
            df: DataFrame with date column
            date_col: Name of date column (default: 'ds')

        Returns:
            DataFrame with temporal features
        """
        df = df.copy()
        dates = pd.to_datetime(df[date_col])
        df["dow"] = dates.dt.dayofweek  # 0=Monday, 6=Sunday
        df["month"] = dates.dt.month
        df["quarter"] = dates.dt.quarter
        df["is_weekend"] = (df["dow"] >= 5).astype(int)
        return df

    def add_external_regressors(self, df: pd.DataFrame, country: Optional[str] = None) -> pd.DataFrame:
        """Add external time series data from database tables.

        Fetches:
        - gdp_growth: GDP growth from CountryIndicatorHistory (World Bank)
        - air_quality: PM2.5 from RegionSignal (OpenAQ)
        - carbon_price: Carbon price ($/tCO2) from RegionSignal

        Data is merged by nearest date (forward-filled for gaps).
        Falls back to zeros if DB is unavailable or no data found.

        Args:
            df: Input DataFrame with 'ds' column
            country: Country/region code for data lookup

        Returns:
            DataFrame with external regressor columns
        """
        df = df.copy()

        # Default zeros — overwritten if DB data available
        df["gdp_growth"] = 0.0
        df["air_quality"] = 0.0
        df["carbon_price"] = 0.0

        if country is None:
            log.debug("No country specified, using placeholder regressors")
            return df

        try:
            gdp_series = self._fetch_gdp_growth(country)
            if not gdp_series.empty:
                df = self._merge_regressor(df, gdp_series, "gdp_growth")

            aq_series = self._fetch_air_quality(country)
            if not aq_series.empty:
                df = self._merge_regressor(df, aq_series, "air_quality")

            carbon_series = self._fetch_carbon_price(country)
            if not carbon_series.empty:
                df = self._merge_regressor(df, carbon_series, "carbon_price")

        except Exception as e:
            log.warning(f"External regressor fetch failed: {e}. Using zeros.")

        regressors_active = sum([
            int((df["gdp_growth"] != 0.0).any()),
            int((df["air_quality"] != 0.0).any()),
            int((df["carbon_price"] != 0.0).any())
        ])
        log.debug(f"External regressors: {regressors_active}/3 active for country={country}")
        return df

    @staticmethod
    def _fetch_gdp_growth(country: str) -> pd.Series:
        """Fetch GDP growth time series from CountryIndicatorHistory."""
        try:
            from app.database import SessionLocal, CountryIndicatorHistory
        except ImportError:
            return pd.Series(dtype=float)

        db = SessionLocal()
        try:
            rows = db.query(CountryIndicatorHistory).filter(
                CountryIndicatorHistory.country_iso3 == country.upper(),
                CountryIndicatorHistory.indicator_code.in_([
                    "NY.GDP.MKTP.KD.ZG",  # GDP growth (annual %)
                    "NY.GDP.PCAP.KD.ZG",  # GDP per capita growth
                ])
            ).order_by(CountryIndicatorHistory.as_of_date.asc()).all()

            if not rows:
                return pd.Series(dtype=float)

            data = {pd.to_datetime(r.as_of_date): r.value for r in rows if r.value is not None}
            return pd.Series(data, name="gdp_growth").sort_index()
        finally:
            db.close()

    @staticmethod
    def _fetch_air_quality(region: str) -> pd.Series:
        """Fetch PM2.5 time series from RegionSignal."""
        try:
            from app.database import SessionLocal, RegionSignal
        except ImportError:
            return pd.Series(dtype=float)

        db = SessionLocal()
        try:
            rows = db.query(RegionSignal).filter(
                RegionSignal.region_code == region.upper(),
                RegionSignal.metric.in_(["pm25", "PM2.5", "pm2_5", "pm25_ugm3"])
            ).order_by(RegionSignal.observed_at.asc()).all()

            if not rows:
                return pd.Series(dtype=float)

            data = {pd.to_datetime(r.observed_at): r.value for r in rows if r.value is not None}
            return pd.Series(data, name="air_quality").sort_index()
        finally:
            db.close()

    @staticmethod
    def _fetch_carbon_price(region: str) -> pd.Series:
        """Fetch carbon price time series ($/tCO2) from RegionSignal.

        Looks for metrics: carbon_price, co2_price, ets_price, carbon_tax
        """
        try:
            from app.database import SessionLocal, RegionSignal
        except ImportError:
            return pd.Series(dtype=float)

        db = SessionLocal()
        try:
            rows = db.query(RegionSignal).filter(
                RegionSignal.region_code == region.upper(),
                RegionSignal.metric.in_([
                    "carbon_price",
                    "co2_price",
                    "ets_price",      # EU Emissions Trading System
                    "carbon_tax",
                    "carbon_price_usd_tco2"
                ])
            ).order_by(RegionSignal.observed_at.asc()).all()

            if not rows:
                return pd.Series(dtype=float)

            data = {pd.to_datetime(r.observed_at): r.value for r in rows if r.value is not None}
            return pd.Series(data, name="carbon_price").sort_index()
        finally:
            db.close()

    @staticmethod
    def _merge_regressor(df: pd.DataFrame, series: pd.Series, col_name: str) -> pd.DataFrame:
        """Merge external time series into DataFrame by nearest date with forward-fill."""
        reg_df = series.reset_index()
        reg_df.columns = ["ds", col_name]
        reg_df["ds"] = pd.to_datetime(reg_df["ds"])

        df["ds"] = pd.to_datetime(df["ds"])
        merged = pd.merge_asof(
            df.sort_values("ds"),
            reg_df.sort_values("ds"),
            on="ds",
            direction="backward",
            suffixes=("_old", "")
        )

        if f"{col_name}_old" in merged.columns:
            merged = merged.drop(columns=[f"{col_name}_old"])

        merged[col_name] = merged[col_name].ffill().fillna(0.0)
        return merged

    def engineer_all(
        self,
        df: pd.DataFrame,
        target_col: str,
        date_col: str = "ds",
        country: Optional[str] = None
    ) -> pd.DataFrame:
        """Apply full feature engineering pipeline.

        Adapts lag periods to dataset size: lags exceeding 60% of data length
        are excluded to prevent all-NaN results.

        Args:
            df: Input DataFrame with date and target columns
            target_col: Name of target column
            date_col: Name of date column (default: 'ds')
            country: Country code for external data (optional)

        Returns:
            DataFrame with all engineered features, NaN rows dropped
        """
        df = df.copy()
        n = len(df)

        # Adaptive lags — only use lags that leave enough non-NaN rows
        max_lag = int(n * 0.6)
        lags = [l for l in [1, 7, 30] if l <= max_lag]
        if not lags:
            lags = [1]

        windows = [w for w in [7, 30] if w <= max_lag]
        if not windows:
            windows = [min(7, max(2, n // 3))]

        # Apply all transformations
        df = self.add_lags(df, target_col, lags=lags)
        df = self.add_rolling_stats(df, target_col, windows=windows)
        df = self.add_seasonality(df, date_col)
        df = self.add_external_regressors(df, country)

        # Drop rows with NaN from lag/rolling operations
        initial_rows = len(df)
        df = df.dropna()
        dropped = initial_rows - len(df)

        log.info(f"Feature engineering complete: {len(df)} rows ({dropped} dropped due to lags/rolling)")
        return df
