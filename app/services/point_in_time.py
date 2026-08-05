"""Reading country_indicator_history as of a past moment.

Issue #75. The table already holds what is needed: `fetched_at` is populated on
every row (95,024 in production across 542 fetch times), the refresh in
app/external_data.py only inserts, and nothing deletes. So a point-in-time
answer is a query, not a new table -- the vintage of every value is already
recorded, and no consumer has ever asked for it.

The distinction this module exists to make:

    latest        what the source says now
    as of D       what this system held on D

A backtest that scores a model against a past date must use the second. Reading
the first credits the model with values published after the date it is being
scored on, which is not a smaller error than a wrong number -- it is a number
that could not have been known.

**One caveat, measured rather than assumed.** `value` and `fetched_at` are
never rewritten, so the value as of D is stable. `as_of_date` is not: the #58
backfill retroactively assigned a period to 45,065 production rows, and the row
keeps no record of what it said before. So the *period a value describes* is
reproducible only until the next such correction. See the issue.
"""

from datetime import datetime
from typing import Dict, Optional

from sqlalchemy import text

# Ordering is (fetched_at DESC, id DESC), never fetched_at alone. Rows written
# in one transaction share fetched_at to the microsecond, and with only
# fetched_at in the ORDER BY the winner is whichever row the database happens to
# return first -- the same non-determinism that made the gdp_growth series
# 4.92 or 5.04 depending on row order (#86). id breaks the tie by write order.
#
# `value IS NOT NULL` because the writer never stores a NULL value: it skips the
# indicator entirely when the fetch yields nothing. A NULL therefore is not an
# observation of "no value", and letting one shadow the real value beneath it
# would report absence where the system did hold something.

_VALUE_AS_OF = text(
    """
    SELECT value
      FROM country_indicator_history
     WHERE country_iso3 = :iso3
       AND indicator_code = :code
       AND fetched_at <= :as_of
       AND value IS NOT NULL
     ORDER BY fetched_at DESC, id DESC
     LIMIT 1
    """
)

# row_number() rather than PostgreSQL's DISTINCT ON: the production database is
# PostgreSQL, but this has to keep working under the SQLite the test suite runs
# on, and a read path that silently changes shape between them is worse than a
# slightly longer query.
_SERIES_AS_OF = text(
    """
    SELECT as_of_date, value
      FROM (
            SELECT as_of_date,
                   value,
                   row_number() OVER (
                       PARTITION BY as_of_date
                       ORDER BY fetched_at DESC, id DESC
                   ) AS rn
              FROM country_indicator_history
             WHERE country_iso3 = :iso3
               AND indicator_code = :code
               AND fetched_at <= :as_of
               AND as_of_date IS NOT NULL
               AND value IS NOT NULL
           ) ranked
     WHERE rn = 1
     ORDER BY as_of_date
    """
)


def value_as_of(
    db,
    country_iso3: str,
    indicator_code: str,
    as_of: datetime,
) -> Optional[float]:
    """The value this system held for (country, indicator) at `as_of`.

    `as_of` is inclusive: a value fetched at exactly that moment was known.

    Returns None when nothing had been fetched yet. **Not 0.0** -- a caller
    that cannot tell "no data" from "zero" is how the gdp_growth regressor
    looked like working data for its entire life (#86).
    """
    row = db.execute(_VALUE_AS_OF, {
        "iso3": country_iso3.upper(),
        "code": indicator_code,
        "as_of": as_of,
    }).first()
    return None if row is None else row[0]


def series_as_of(
    db,
    country_iso3: str,
    indicator_code: str,
    as_of: datetime,
) -> Dict[datetime, float]:
    """Each observation period, carrying the value in force at `as_of`.

    Periods first published after `as_of` are absent, and a period revised
    after it carries the earlier value -- the series as it stood, not as it
    reads now.

    Rows with no `as_of_date` are excluded: they have no period to attach a
    value to, and much of the table carries none (#58).

    Ordered oldest period first.
    """
    rows = db.execute(_SERIES_AS_OF, {
        "iso3": country_iso3.upper(),
        "code": indicator_code,
        "as_of": as_of,
    }).all()
    return {period: value for period, value in rows}
