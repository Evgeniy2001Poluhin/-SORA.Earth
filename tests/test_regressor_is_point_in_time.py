"""The gdp_growth regressor reads what was knowable, not what is known.

Issue #75. The point-in-time reader has existed since #90 and nothing called
it: `_fetch_gdp_growth` selected the newest row for each period regardless of
when it was fetched. For a live forecast that is right -- predicting forward
from the newest figures reads nothing unknowable. For a backtest it is the
leakage the issue is about, and the two share one code path.

What makes this worth a test rather than a review note: the leak does not look
like an error. A World Bank figure for 2019 may have first appeared in 2021 and
been revised twice since, so a model scored at 2020-01-01 against today's table
is credited with a number nobody had. The score improves. Nothing raises, no
column is empty, no warning fires -- the failure is indistinguishable from the
model being good.

So each case here fixes a revision in time and asserts the *earlier* value is
returned, which is false whenever the bound is dropped.
"""
from datetime import datetime

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.database as database
from app.services.forecasting.features import GDP_GROWTH_INDICATOR, FeatureEngineer

PERIOD = datetime(2019, 1, 1)

# Two fetches of the same period: the figure as first published, and as revised.
FIRST_PUBLISHED = datetime(2021, 7, 1)
REVISED = datetime(2023, 3, 1)


@pytest.fixture
def indicator_db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    database.CountryIndicatorHistory.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", Session)
    return Session


def _insert(Session, rows, source="world_bank"):
    db = Session()
    try:
        for as_of_date, value, fetched_at in rows:
            db.add(database.CountryIndicatorHistory(
                country_iso3="RUS",
                indicator_code=GDP_GROWTH_INDICATOR,
                value=value,
                source=source,
                as_of_date=as_of_date,
                fetched_at=fetched_at,
                refresh_job_name="test",
            ))
        db.commit()
    finally:
        db.close()


def _revised_series(Session):
    _insert(Session, [
        (PERIOD, 2.20, FIRST_PUBLISHED),
        (PERIOD, 1.30, REVISED),
    ])


def test_a_backtest_before_the_revision_sees_the_first_figure(indicator_db):
    """The case the issue is about, at its smallest."""
    _revised_series(indicator_db)

    series = FeatureEngineer._fetch_gdp_growth(
        "RUS", as_of=datetime(2022, 1, 1))

    assert series[pd.Timestamp(PERIOD)] == pytest.approx(2.20), (
        "the revision of 2023 is visible to a backtest scoring 2022"
    )


def test_after_the_revision_the_same_query_sees_the_new_figure(indicator_db):
    """The bound is a bound, not a preference for old rows.

    Without this, `_fetch_gdp_growth` could satisfy the case above by always
    returning the oldest row and would look correct.
    """
    _revised_series(indicator_db)

    series = FeatureEngineer._fetch_gdp_growth(
        "RUS", as_of=datetime(2024, 1, 1))

    assert series[pd.Timestamp(PERIOD)] == pytest.approx(1.30)


def test_a_period_not_yet_fetched_is_absent_not_zero(indicator_db):
    """Absent, not defaulted.

    A period that reads 0.0 is a claim about the economy. The column's default
    is 0.0 for want of anything better, and a regressor that silently supplied
    it for periods nobody had yet fetched would be #86 again: data that looks
    present because a plausible number is sitting where it would be.
    """
    _insert(indicator_db, [(PERIOD, 2.20, FIRST_PUBLISHED)])

    series = FeatureEngineer._fetch_gdp_growth(
        "RUS", as_of=datetime(2020, 1, 1))

    assert series.empty, f"a value fetched in 2021 reached a 2020 read: {dict(series)}"


def test_the_live_path_still_reads_the_newest_figure(indicator_db):
    """No `as_of` means now, and now is right for a forecast.

    The change must not turn live prediction into a read of stale figures --
    that would be a regression dressed as rigour.
    """
    _revised_series(indicator_db)

    series = FeatureEngineer._fetch_gdp_growth("RUS")

    assert series[pd.Timestamp(PERIOD)] == pytest.approx(1.30)


def test_a_fallback_value_is_not_read_as_measured(indicator_db):
    """#86's guarantee, which moved from an ORM filter to an argument.

    The fallback chain writes static benchmark figures under the same indicator
    code when a fetch fails. No such row exists today -- gdp_growth is in
    neither BENCHMARKS nor GLOBAL_AVG -- so the property rests on configuration
    elsewhere unless the query states it.
    """
    _insert(indicator_db, [(PERIOD, 2.20, FIRST_PUBLISHED)])
    _insert(indicator_db, [(PERIOD, 9.99, REVISED)], source="benchmark_fallback")

    series = FeatureEngineer._fetch_gdp_growth(
        "RUS", as_of=datetime(2024, 1, 1))

    assert series[pd.Timestamp(PERIOD)] == pytest.approx(2.20), (
        "a fallback figure is being served as a measured one"
    )


def test_the_frame_built_for_a_past_date_carries_the_past_figure(indicator_db):
    """End to end, because the argument has to survive two call sites.

    `_fetch_gdp_growth` taking `as_of` proves nothing if `engineer_all` drops
    it on the way down -- and a dropped optional argument is silent.
    """
    _revised_series(indicator_db)
    frame = pd.DataFrame({
        "ds": pd.date_range("2019-01-01", periods=40, freq="D"),
        "y": range(40),
    })

    built = FeatureEngineer().engineer_all(
        frame, target_col="y", country="RUS", as_of=datetime(2022, 1, 1))

    # The set of distinct values, not an elementwise comparison: a Series
    # compared against `approx` collapses to one bool and `.all()` on it is
    # true for any non-empty frame, so the first version of this line passed
    # while reporting the values it was supposed to be checking.
    assert sorted(set(built["gdp_growth"])) == pytest.approx([2.20]), (
        f"as_of did not reach the regressor: "
        f"{sorted(set(built['gdp_growth']))}"
    )
