"""The gdp_growth regressor: what it asks for must be what the ingester collects.

Issue #86. `_fetch_gdp_growth` queried two indicator codes that
`country_indicator_history` has never held, because `INDICATORS` in
app/external_data.py -- the only thing that writes that table -- does not
collect them. The series came back empty, `_merge_regressor` was never reached,
and `df["gdp_growth"]` kept its 0.0 default in every forecast ever produced.

Nothing caught it. An empty series is the legitimate outcome for a country with
no data, so "no data for this country" and "this code has never worked" look
identical from inside. The existing test asserted only that the column exists,
which a column of zeros satisfies perfectly.

These tests run against a real in-memory SQLite database holding the production
model, so the SQLAlchemy filter actually executes. A mocked query chain returns
whatever rows the mock was handed and would prove nothing about which rows the
filter selects -- and that is the entire question here.
"""

from datetime import datetime

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.database as database
from app.external_data import INDICATORS
from app.services.forecasting.features import GDP_GROWTH_INDICATOR, FeatureEngineer

# The other annual growth series World Bank publishes. It is a different
# quantity -- per capita -- and must not end up in a column named gdp_growth.
PER_CAPITA_GROWTH = "NY.GDP.PCAP.KD.ZG"


@pytest.fixture
def indicator_db(monkeypatch):
    """A real database holding only country_indicator_history."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    database.CountryIndicatorHistory.__table__.create(engine)
    # The other two regressors read region_signals. Without it they raise, the
    # broad `except` swallows it, and the test would pass over a silenced error.
    database.RegionSignal.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", Session)
    return Session


def _insert(Session, rows):
    db = Session()
    try:
        for code, as_of, value in rows:
            db.add(database.CountryIndicatorHistory(
                country_iso3="RUS",
                country_name="Russia",
                indicator_code=code,
                indicator_name="test",
                value=value,
                source="world_bank",
                as_of_date=as_of,
                fetched_at=datetime(2026, 8, 5),
                refresh_job_name="test",
            ))
        db.commit()
    finally:
        db.close()


def test_requested_indicator_is_collected_by_the_ingester():
    """The regressor asks for a code nothing writes -- this is #86 itself.

    Reads the constant the query actually uses rather than restating the code,
    so the test cannot pass while the query asks for something else.
    """
    assert GDP_GROWTH_INDICATOR in INDICATORS.values(), (
        f"the feature builder requests {GDP_GROWTH_INDICATOR}, which the "
        f"ingester does not collect, so the column is always 0.0. "
        f"Collected: {sorted(INDICATORS.values())}"
    )


def test_series_is_gdp_growth_alone_not_a_blend(indicator_db):
    """Both codes are annual and share a date; a dict keyed by date drops one.

    Which one survived depended on row order, so the column was GDP growth or
    per-capita growth unpredictably.
    """
    _insert(indicator_db, [
        (GDP_GROWTH_INDICATOR, datetime(2024, 1, 1), 4.92),
        (PER_CAPITA_GROWTH, datetime(2024, 1, 1), 5.04),
    ])

    series = FeatureEngineer._fetch_gdp_growth("RUS")

    assert len(series) == 1
    assert series.iloc[0] == pytest.approx(4.92), (
        "the series carries per-capita growth, or a mix of the two"
    )


def test_rows_without_a_period_are_excluded(indicator_db):
    """A row with no as_of_date becomes a NaT key, and NaT keys collapse.

    The backfill (#58) established that many rows carry no period at all, so
    this is the normal state of the table, not an edge case.
    """
    _insert(indicator_db, [
        (GDP_GROWTH_INDICATOR, None, 1.0),
        (GDP_GROWTH_INDICATOR, None, 2.0),
        (GDP_GROWTH_INDICATOR, datetime(2024, 1, 1), 4.92),
    ])

    series = FeatureEngineer._fetch_gdp_growth("RUS")

    assert not series.index.isna().any(), "an undated row became a NaT point"
    assert len(series) == 1
    assert series.iloc[0] == pytest.approx(4.92)


def test_regressor_column_carries_the_value_not_zero(indicator_db):
    """End-to-end: asserting the column exists is what let #86 through.

    Note what this also shows. One stored point becomes the same value on every
    row, because merge_asof carries it forward -- a constant column. Measured
    on production, country_indicator_history holds on average 1.0 dated point
    per country per indicator (at most 2): the refresh stores only the latest
    World Bank value, never the 66-year series the API publishes.

    So collecting the indicator stops the column being identically 0.0, but it
    does not yet make it informative -- a constant regressor has no variance
    and cannot carry a coefficient. Storing the historical series is the
    remaining work, and it is the same vintage problem as #75.
    """
    _insert(indicator_db, [(GDP_GROWTH_INDICATOR, datetime(2025, 1, 1), 4.92)])

    df = pd.DataFrame({
        "ds": pd.date_range(start="2026-01-01", periods=10, freq="D"),
        "y": range(10),
    })

    result = FeatureEngineer().add_external_regressors(df, country="RUS")

    # Compare the list, not `Series == approx(...)`: that returns a scalar
    # False rather than an elementwise mask, and the test would fail whatever
    # the column held.
    assert result["gdp_growth"].tolist() == pytest.approx([4.92] * 10), (
        "gdp_growth stayed at its 0.0 default despite data being present"
    )
