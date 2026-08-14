"""Time series feature engineering for forecasting models."""

from datetime import datetime
from typing import List, Optional
import logging

import pandas as pd
import numpy as np

log = logging.getLogger(__name__)

# The indicator the gdp_growth regressor is built from. INDICATORS in
# app/external_data.py -- the only writer of country_indicator_history -- must
# collect this code, or the column silently stays at its 0.0 default. See #86;
# tests/test_gdp_growth_regressor.py binds the two together.
GDP_GROWTH_INDICATOR = "NY.GDP.MKTP.KD.ZG"  # GDP growth (annual %)


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
        """Add lagged features (t-1, t-7, t-14).

        Args:
            df: Input DataFrame with target column
            target_col: Name of target column
            lags: List of lag periods (default: [1, 7, 14])

        Returns:
            DataFrame with added lag columns
        """
        if lags is None:
            lags = [1, 7, 14]

        df = df.copy()
        for lag in lags:
            df[f"{target_col}_lag{lag}"] = df[target_col].shift(lag)
        return df

    def add_rolling_stats(self, df: pd.DataFrame, target_col: str, windows: List[int] = None) -> pd.DataFrame:
        """Add rolling statistics (mean, std) over windows.

        Args:
            df: Input DataFrame
            target_col: Target column name
            windows: Window sizes (default: [7, 14])

        Returns:
            DataFrame with rolling mean and std columns
        """
        if windows is None:
            windows = [7, 14]

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

    def add_external_regressors(self, df: pd.DataFrame,
                               country: Optional[str] = None,
                               as_of: Optional[datetime] = None) -> pd.DataFrame:
        """Add external time series data from database tables.

        One regressor, not three. `air_quality` and `carbon_price` were
        removed in #95 rather than left at zero:

        - `air_quality` read `region_signals` for pm25. That table holds six
          metrics and none is air quality. PM2.5 does exist, in
          `environmental_observations` under `pm2_5`, but the join is not a
          rename: region keys are country codes for nineteen of twenty-one
          regions and city codes (RU-MOW, RU-SPE) for the rest, and there were
          eight hours of observations at the time of writing. Merging that
          onto a multi-month frame produces a step at the join boundary, which
          a model will happily fit.
        - `carbon_price` read `region_signals` for carbon prices. There are
          none, anywhere in the database. No ingester writes them.

        A column of zeros is not a neutral placeholder. Zero is an economically
        meaningful value for both of these -- clean air, no carbon price -- so
        a model cannot tell "we have no data" from "the figure is zero", and
        neither could anyone reading the frame. Declaring a regressor the
        platform does not have is worse than declaring one fewer.

        `gdp_growth` stays: it carries real data after #96, verified across
        all 30 countries on production with variance > 0.

        Args:
            df: Input DataFrame with 'ds' column
            country: Country/region code for data lookup
            as_of: read the regressor as this system held it at this moment.
                Defaults to now. A backtest must pass the date it scores; see
                `_fetch_gdp_growth` and #75.

        Returns:
            DataFrame with the gdp_growth column added
        """
        df = df.copy()

        # The default is still 0.0, and still a compromise -- but a declared
        # one, for a single regressor whose data is known to exist. When it is
        # absent the warning below says so rather than leaving a plausible
        # number in place unremarked.
        df["gdp_growth"] = 0.0

        if country is None:
            log.debug("No country specified, using placeholder regressors")
            return df

        try:
            gdp_series = self._fetch_gdp_growth(country, as_of)
            if not gdp_series.empty:
                df = self._merge_regressor(df, gdp_series, "gdp_growth")
        except Exception as e:
            log.warning(f"External regressor fetch failed: {e}. Using zeros.")

        # This count existed before #86 and was logged at debug, so a regressor
        # that had never once carried a value looked exactly like one that was
        # working. A column left entirely at its default contributes nothing to
        # the forecast, and that is worth saying out loud.
        if not (df["gdp_growth"] != 0.0).any():
            log.warning(
                "gdp_growth carries no data for country=%s: the column is all "
                "zeros and contributes nothing.", country,
            )
        return df

    @staticmethod
    def _fetch_gdp_growth(country: str,
                          as_of: Optional[datetime] = None) -> pd.Series:
        """GDP growth for `country`, as this system held it at `as_of`.

        `as_of` defaults to now, which is right for a live forecast: predicting
        forward from the newest figures reads nothing it could not know.

        A backtest must pass the date it is scoring, and this is why (#75).
        Reading today's figures to score a model at a past date credits it with
        values published after that date -- a value it could not have had, which
        is a different and worse failure than a wrong one, because the error
        flatters the model instead of showing up as one.
        """
        try:
            from app.database import SessionLocal
            from app.services.point_in_time import series_as_of
        except ImportError:
            return pd.Series(dtype=float)

        db = SessionLocal()
        try:
            # One indicator, not two. Both annual growth series share a date,
            # and the series is keyed by date, so a second code would overwrite
            # the first -- leaving the column as GDP growth or per-capita growth
            # depending on row order. They are different quantities; only the
            # one this regressor is named after belongs.
            #
            # `source` is passed rather than left to the table's contents. The
            # fallback chain writes static benchmark figures under the same
            # indicator code when a fetch fails, and a model must not silently
            # train on a stand-in. No such row exists today -- gdp_growth is in
            # neither BENCHMARKS nor GLOBAL_AVG, and refresh_indicator_history
            # writes source='world_bank' unconditionally -- but that is a
            # property of two things a future edit could change without
            # noticing (#86).
            #
            # Undated rows are dropped by series_as_of: they have no period to
            # attach a value to, much of the table carries none (#58), and
            # pd.to_datetime(None) is NaT, so every undated row would collapse
            # onto one key and merge against nothing.
            series = series_as_of(
                db,
                country,
                GDP_GROWTH_INDICATOR,
                as_of if as_of is not None else datetime.utcnow(),
                source="world_bank",
            )
            if not series:
                return pd.Series(dtype=float)

            # A period fetched twice is a revision; series_as_of keeps the one
            # in force at `as_of`, broken by id rather than by whichever row the
            # database happened to return (#86).
            data = {pd.to_datetime(period): value
                    for period, value in series.items()}
            return pd.Series(data, name="gdp_growth").sort_index()
        finally:
            db.close()

    # _fetch_air_quality and _fetch_carbon_price lived here and were removed
    # with their regressors (#95). Both read `region_signals` for metrics that
    # table has never held, so both returned an empty series on every call and
    # the columns stayed at 0.0 in every forecast the platform has produced.
    #
    # Keeping working-looking code for a feature that was withdrawn is how the
    # next person concludes the data is merely missing rather than absent by
    # decision. The reasoning is in add_external_regressors and in #95.

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
        country: Optional[str] = None,
        as_of: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """Apply full feature engineering pipeline.

        Adapts lag periods to dataset size: lags exceeding 60% of data length
        are excluded to prevent all-NaN results.

        Args:
            df: Input DataFrame with date and target columns
            target_col: Name of target column
            date_col: Name of date column (default: 'ds')
            country: Country code for external data (optional)
            as_of: read external regressors as of this moment rather than now.
                A backtest passes the date it scores, so the frame carries the
                figures that were knowable then (#75).

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
        df = self.add_external_regressors(df, country, as_of)

        # Drop rows with NaN from lag/rolling operations
        initial_rows = len(df)
        df = df.dropna()
        dropped = initial_rows - len(df)

        log.info(f"Feature engineering complete: {len(df)} rows ({dropped} dropped due to lags/rolling)")
        return df
