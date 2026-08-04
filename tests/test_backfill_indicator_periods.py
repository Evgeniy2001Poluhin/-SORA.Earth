"""The year-matching rule, enumerated.

This decides whether a real measurement gets a real date or a fabricated one, so
the rule is walked over its whole space rather than sampled. A wrong date is
worse than the NULL it replaces: a NULL is read as "unknown" and a date is
believed.

The API-facing parts are not tested here; they are network behaviour, and the
script's own pacing and retry logic is exercised against production rather than
against a fake that would agree with whatever it was told.
"""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "backfill_indicator_periods",
    Path(__file__).resolve().parents[1] / "scripts" / "backfill_indicator_periods.py",
)
backfill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill)

match_year = backfill.match_year


# A real series, from SAU/NY.GDP.PCAP.CD as the API returns it today.
HISTORY = {
    "2025": 34536.6555456551,
    "2024": 35527.786181866,
    "2023": 36156.8562500172,
    "2022": 38510.2276211037,
    "2021": 31920.7653655643,
}


def test_the_rounded_value_the_ingester_stored_still_matches():
    """Values are stored rounded, so an exact comparison would match nothing.

    This is the case the whole backfill rests on: 34536.66 in the table against
    34536.6555456551 from the source.
    """
    assert match_year(34536.66, HISTORY) == "2025"


def test_a_value_the_source_no_longer_reports_matches_nothing():
    """Measured on production: SAU/NY.GDP.PCAP.CD holds 35121.66, which appears
    against no year the API offers today -- the figures were revised after the
    row was written.

    The value is real and its year is simply not discoverable any more. Anything
    other than "no match" here would put a fabricated date on a real
    measurement.
    """
    assert match_year(35121.66, HISTORY) is None


def test_a_value_occurring_in_two_years_identifies_neither():
    """A match that could be either identifies nothing, and guessing the newer
    one would be a coin toss recorded as a fact."""
    ambiguous = {"2024": 100.0, "2023": 100.0, "2022": 55.0}
    assert match_year(100.0, ambiguous) is None
    assert match_year(55.0, ambiguous) == "2022"


def test_an_empty_history_matches_nothing():
    assert match_year(1.0, {}) is None


@pytest.mark.parametrize("value,expected", [
    (34536.6555456551, "2025"),   # exact
    (34536.66, "2025"),           # as stored, rounded to two places
    (34536.6, None),              # rounded further than the ingester ever did
    (34537.0, None),              # a different figure entirely
])
def test_the_tolerance_admits_the_stored_rounding_and_no_more(value, expected):
    """The boundary matters in both directions.

    Too tight and every row is unrecoverable; too loose and neighbouring years
    collide, which is how one measurement acquires another's date.
    """
    assert match_year(value, HISTORY) == expected


@pytest.mark.parametrize("value,history,expected", [
    # Percentages, where the absolute floor governs.
    (0.1, {"2024": 0.1, "2023": 0.2}, "2024"),
    # 0.104 rounds to 0.10, so a stored 0.1 is genuinely consistent with it.
    # I first wrote None here and the code was right: the tolerance must admit
    # every source value the stored figure could have come from, and two
    # decimal places cannot distinguish 0.100 from 0.104.
    (0.1, {"2024": 0.104, "2023": 0.2}, "2024"),
    # 0.106 rounds to 0.11, so it cannot be the origin of a stored 0.1.
    (0.1, {"2024": 0.106, "2023": 0.2}, None),
    # Large figures, where the relative part governs.
    (1_000_000.0, {"2024": 1_000_000.0004, "2023": 2.0}, "2024"),
    (1_000_000.0, {"2024": 1_000_100.0, "2023": 2.0}, None),
])
def test_the_tolerance_covers_what_the_stored_rounding_could_have_hidden(
    value, history, expected
):
    """These indicators span dollars per capita and percentages of a whole, so
    the tolerance is relative with an absolute floor.

    The floor is the rounding the ingester applied -- two decimal places, so
    half a hundredth. It has to be at least that or genuine matches are missed;
    it must not be more or neighbouring years collide, which is how one
    measurement acquires another's date.
    """
    assert match_year(value, history) == expected


def test_two_source_values_that_both_round_to_the_stored_one_identify_neither():
    """The other side of the tolerance above.

    When rounding has genuinely destroyed the distinction, the answer is that
    the year is not recoverable -- not the nearer of the two.
    """
    assert match_year(0.1, {"2024": 0.098, "2023": 0.103}) is None


def test_zero_is_matched_rather_than_treated_as_missing():
    """Zero is a legitimate reading -- renewables share, for one -- and a
    relative tolerance collapses to nothing at zero, so the absolute floor is
    what carries it."""
    assert match_year(0.0, {"2024": 0.0, "2023": 5.0}) == "2024"


def test_a_negative_value_is_matched_on_magnitude():
    """Net balances go negative, and a tolerance derived from a signed value
    would be negative too, matching nothing at all."""
    assert match_year(-1234.56, {"2024": -1234.5612, "2023": 8.0}) == "2024"
