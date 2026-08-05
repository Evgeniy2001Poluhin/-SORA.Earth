"""Reading country_indicator_history as of a past moment.

Issue #75. Measured before writing any of this: the table already keeps every
row it has ever written -- `fetched_at` is populated on all 95,024 production
rows across 542 distinct fetch times, the refresh only ever inserts, and no
path deletes. So a point-in-time answer needs no new table; it needs a query
that asks for one, a guarantee that the property keeps holding, and an index.

What these tests pin is the *reading* semantics, which nothing expressed
before. `app/services/forecasting/features.py` takes whatever the table
returns, which is the latest vintage -- so a backtest over a past date silently
uses values published after that date.

They run against a real migrated PostgreSQL: the table carries CHECK
constraints on the period columns, and a query written against a mock would
never meet them.
"""

from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.point_in_time import series_as_of, value_as_of
from tests.postgres_scratch import (  # noqa: F401  (scratch_db is a fixture)
    requires_postgres,
    scratch_db,
)

pytestmark = requires_postgres

INSERT = text(
    "INSERT INTO country_indicator_history "
    "  (country_iso3, indicator_code, source, value, as_of_date, fetched_at) "
    "VALUES (:iso3, :code, 'world_bank', :value, :as_of_date, :fetched_at)"
)

CODE = "NY.GDP.MKTP.KD.ZG"


@pytest.fixture
def db(scratch_db):
    engine, _ = scratch_db
    with Session(engine) as session:
        yield session


def _write(db, rows, iso3="RUS", code=CODE):
    """rows: (period, value, fetched_at). period may be None."""
    for period, value, fetched_at in rows:
        db.execute(INSERT, {
            "iso3": iso3, "code": code, "value": value,
            "as_of_date": period, "fetched_at": fetched_at,
        })
    db.commit()


def test_a_value_fetched_after_the_cutoff_is_invisible(db):
    """The whole point: what was published later cannot inform a past date."""
    _write(db, [
        (datetime(2024, 1, 1), 4.92, datetime(2026, 6, 1)),
        (datetime(2024, 1, 1), 9.99, datetime(2026, 8, 1)),
    ])

    assert value_as_of(db, "RUS", CODE, datetime(2026, 7, 1)) == pytest.approx(4.92)


def test_a_revision_resolves_to_what_was_current_then(db):
    """A source revising a published value is the case the issue exists for."""
    _write(db, [
        (datetime(2024, 1, 1), 4.92, datetime(2026, 6, 1)),
        (datetime(2024, 1, 1), 5.10, datetime(2026, 7, 1)),
        (datetime(2024, 1, 1), 5.30, datetime(2026, 8, 1)),
    ])

    assert value_as_of(db, "RUS", CODE, datetime(2026, 6, 15)) == pytest.approx(4.92)
    assert value_as_of(db, "RUS", CODE, datetime(2026, 7, 15)) == pytest.approx(5.10)
    assert value_as_of(db, "RUS", CODE, datetime(2026, 9, 1)) == pytest.approx(5.30)


def test_the_cutoff_is_inclusive(db):
    """A value fetched exactly at the cutoff was known at the cutoff."""
    _write(db, [(datetime(2024, 1, 1), 4.92, datetime(2026, 6, 1))])

    assert value_as_of(db, "RUS", CODE, datetime(2026, 6, 1)) == pytest.approx(4.92)


def test_nothing_fetched_before_the_cutoff_is_not_zero(db):
    """Absence must be distinguishable from a value.

    Returning 0.0 for "we knew nothing yet" is how the gdp_growth regressor
    came to look like working data for its whole life (#86).
    """
    _write(db, [(datetime(2024, 1, 1), 4.92, datetime(2026, 8, 1))])

    assert value_as_of(db, "RUS", CODE, datetime(2026, 7, 1)) is None


def test_other_countries_and_indicators_are_excluded(db):
    _write(db, [(datetime(2024, 1, 1), 4.92, datetime(2026, 6, 1))])
    _write(db, [(datetime(2024, 1, 1), 1.11, datetime(2026, 6, 1))], iso3="DEU")
    _write(db, [(datetime(2024, 1, 1), 2.22, datetime(2026, 6, 1))],
           code="SI.POV.GINI")

    assert value_as_of(db, "RUS", CODE, datetime(2026, 7, 1)) == pytest.approx(4.92)


def test_series_gives_one_point_per_period_current_at_the_cutoff(db):
    """Each period resolved independently -- the series a backtest needs."""
    _write(db, [
        (datetime(2023, 1, 1), 3.00, datetime(2026, 6, 1)),
        (datetime(2023, 1, 1), 3.50, datetime(2026, 8, 1)),   # revised later
        (datetime(2024, 1, 1), 4.92, datetime(2026, 6, 1)),
        (datetime(2025, 1, 1), 1.00, datetime(2026, 8, 1)),   # published later
    ])

    series = series_as_of(db, "RUS", CODE, datetime(2026, 7, 1))

    assert list(series.keys()) == [datetime(2023, 1, 1), datetime(2024, 1, 1)]
    assert series[datetime(2023, 1, 1)] == pytest.approx(3.00)
    assert series[datetime(2024, 1, 1)] == pytest.approx(4.92)


def test_undated_rows_are_absent_from_the_series(db):
    """A row with no period has nothing to attach a value to.

    Much of the table carries no period (#58), and pandas turns a NULL into
    NaT, which collapses every such row onto one key.
    """
    _write(db, [
        (None, 7.77, datetime(2026, 6, 1)),
        (datetime(2024, 1, 1), 4.92, datetime(2026, 6, 1)),
    ])

    series = series_as_of(db, "RUS", CODE, datetime(2026, 7, 1))

    assert list(series.keys()) == [datetime(2024, 1, 1)]


def test_rewriting_a_value_is_refused(db):
    """The property the read depends on, enforced rather than assumed."""
    _write(db, [(datetime(2024, 1, 1), 4.92, datetime(2026, 6, 1))])

    with pytest.raises(Exception) as exc:
        db.execute(text("UPDATE country_indicator_history SET value = 9.99"))
        db.commit()
    assert "written once" in str(exc.value)
    db.rollback()

    assert value_as_of(db, "RUS", CODE, datetime(2026, 7, 1)) == pytest.approx(4.92)


def test_rewriting_the_vintage_is_refused(db):
    """fetched_at is what a point-in-time read resolves on."""
    _write(db, [(datetime(2024, 1, 1), 4.92, datetime(2026, 6, 1))])

    with pytest.raises(Exception) as exc:
        db.execute(text(
            "UPDATE country_indicator_history SET fetched_at = '2026-01-01'"))
        db.commit()
    assert "written once" in str(exc.value)
    db.rollback()


def test_correcting_a_period_is_still_allowed(db):
    """The #58 backfill assigned periods to 45,065 production rows.

    A guard that forbade that would forbid the one maintenance path this table
    has, so the trigger has to permit it -- and this is the test that says so
    rather than leaving it to be discovered by a failing backfill.
    """
    _write(db, [(None, 4.92, datetime(2026, 6, 1))])

    db.execute(text(
        "UPDATE country_indicator_history "
        "SET as_of_date = '2024-01-01', period_status = 'recovered_inferred'"))
    db.commit()

    series = series_as_of(db, "RUS", CODE, datetime(2026, 7, 1))
    assert series[datetime(2024, 1, 1)] == pytest.approx(4.92)


def test_a_value_set_to_null_is_also_refused(db):
    """`value` is nullable, and `<>` against NULL yields NULL, not true.

    Written with `<>` the trigger would let this through silently, which is
    exactly the rewrite worth catching.
    """
    _write(db, [(datetime(2024, 1, 1), 4.92, datetime(2026, 6, 1))])

    with pytest.raises(Exception) as exc:
        db.execute(text("UPDATE country_indicator_history SET value = NULL"))
        db.commit()
    assert "written once" in str(exc.value)
    db.rollback()


def test_a_tie_on_fetched_at_is_broken_deterministically(db):
    """Two rows written in one transaction share fetched_at to the microsecond.

    `ORDER BY fetched_at DESC` alone leaves the winner to whatever the database
    returns first, which is how the same query produced 4.92 or 5.04 in #86.
    """
    same = datetime(2026, 6, 1, 12, 0, 0)
    _write(db, [
        (datetime(2024, 1, 1), 1.00, same),
        (datetime(2024, 1, 1), 2.00, same),
    ])

    first = value_as_of(db, "RUS", CODE, datetime(2026, 7, 1))
    for _ in range(5):
        assert value_as_of(db, "RUS", CODE, datetime(2026, 7, 1)) == first
    # Last row inserted is the later write, so it is the one in force.
    assert first == pytest.approx(2.00)
