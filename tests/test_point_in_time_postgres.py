"""Reading country_indicator_history as of a past moment.

Issue #75. Measured before writing any of this: the table already keeps every
row it has ever written -- `fetched_at` is populated on all 95,024 production
rows across 542 distinct fetch times, the refresh only ever inserts, and no
path deletes. So a point-in-time answer needs no new table; it needs a query
that asks for one, a guarantee that the property keeps holding, and an index.

What these tests pin is the *reading* semantics, which nothing expressed
before. `app/services/forecasting/features.py` used to take whatever the table
returned, which is the latest vintage, so a backtest over a past date silently
used values published after it; it now reads through `series_as_of` and
tests/test_regressor_is_point_in_time.py holds it there.

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


def test_correcting_a_period_is_refused(db):
    """This asserted the opposite until #164, and the reason it changed is
    measurement rather than preference.

    The #58 backfill assigned periods to 45,065 rows, and the trigger had to
    permit that or the table's one maintenance path would have been forbidden.
    So the exception was correct when it was written.

    Measured on production 2026-08-14: `period_run_id` holds **one** distinct
    value across 104,309 rows. Period correction happened once, in a single run
    on 2026-08-05, and has not happened since. It is an event, not a process.

    What the permission cost is that the value/period pairing is not
    reproducible: `value` and `fetched_at` cannot be rewritten, so a
    point-in-time read is exact -- but which period a value was attributed to
    can change under it, silently, and the row keeps no record of what it said
    before.

    Refused now. A future correction is still possible and becomes a decision
    that lifts the guard, which is a thing someone reviews, rather than an
    UPDATE nobody sees.
    """
    _write(db, [(None, 4.92, datetime(2026, 6, 1))])

    with pytest.raises(Exception) as exc:
        db.execute(text(
            "UPDATE country_indicator_history SET as_of_date = '2024-01-01'"))
        db.commit()
    assert "written once" in str(exc.value)
    db.rollback()


def test_the_columns_that_may_still_change_are_untouched(db):
    """The guard must not become "nothing about this row may change".

    `period_status` and the other provenance columns describe how a period was
    established. Freezing those alongside the period would forbid recording
    that a row was reviewed, which is not what #164 asked for.
    """
    _write(db, [(datetime(2024, 1, 1), 4.92, datetime(2026, 6, 1))])

    db.execute(text(
        "UPDATE country_indicator_history "
        "SET period_status = 'recovered_inferred', period_method = 'manual'"))
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


# --- restricting the answer to one source ------------------------------------
#
# `country_indicator_history` is written by one refresh today, but the fallback
# chain in app/external_data.py can store a static benchmark figure under a real
# indicator code when a fetch fails. A caller that must not train on a stand-in
# has to be able to say so; leaving the guarantee to what the table happens to
# contain makes it a property of configuration elsewhere (#86).

INSERT_SOURCED = text(
    "INSERT INTO country_indicator_history "
    "  (country_iso3, indicator_code, source, value, as_of_date, fetched_at) "
    "VALUES (:iso3, :code, :source, :value, :as_of_date, :fetched_at)"
)


def _write_sourced(db, rows, iso3="RUS", code=CODE):
    """rows: (period, value, fetched_at, source)."""
    for period, value, fetched_at, source in rows:
        db.execute(INSERT_SOURCED, {
            "iso3": iso3, "code": code, "value": value, "source": source,
            "as_of_date": period, "fetched_at": fetched_at,
        })
    db.commit()


def test_a_named_source_excludes_a_newer_row_from_another(db):
    """The fallback is newer, so without the filter it wins on every axis."""
    _write_sourced(db, [
        (datetime(2024, 1, 1), 4.92, datetime(2026, 6, 1), "world_bank"),
        (datetime(2024, 1, 1), 9.99, datetime(2026, 7, 1), "benchmark_fallback"),
    ])
    as_of = datetime(2026, 8, 1)

    assert value_as_of(db, "RUS", CODE, as_of,
                       source="world_bank") == pytest.approx(4.92)
    assert series_as_of(db, "RUS", CODE, as_of,
                        source="world_bank")[datetime(2024, 1, 1)] == pytest.approx(4.92)


def test_omitting_the_source_answers_from_everything_stored(db):
    """The filter is opt-in, and the default has to stay what it was.

    Silently restricting to one source would change every existing caller's
    answer without any of them saying anything.
    """
    _write_sourced(db, [
        (datetime(2024, 1, 1), 4.92, datetime(2026, 6, 1), "world_bank"),
        (datetime(2024, 1, 1), 9.99, datetime(2026, 7, 1), "benchmark_fallback"),
    ])
    as_of = datetime(2026, 8, 1)

    assert value_as_of(db, "RUS", CODE, as_of) == pytest.approx(9.99)
    assert series_as_of(db, "RUS", CODE, as_of)[datetime(2024, 1, 1)] == pytest.approx(9.99)


def test_a_source_with_nothing_stored_answers_nothing(db):
    """Not the value from another source, which is the failure that matters."""
    _write_sourced(db, [
        (datetime(2024, 1, 1), 4.92, datetime(2026, 6, 1), "world_bank"),
    ])
    as_of = datetime(2026, 8, 1)

    assert value_as_of(db, "RUS", CODE, as_of, source="rosstat") is None
    assert series_as_of(db, "RUS", CODE, as_of, source="rosstat") == {}
