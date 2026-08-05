"""The year-matching rule, enumerated.

This decides whether a real measurement gets a real date or a fabricated one, so
the rule is walked over its whole space rather than sampled. A wrong date is
worse than the NULL it replaces: a NULL is read as "unknown" and a date is
believed.

The API-facing parts are not tested here. They are covered in
`test_backfill_integration_postgres.py`, which runs the script itself against a
localhost stub and a real PostgreSQL -- paging, an unobtainable page, the
absence of any date bound on the request, and the write path.

This file was previously said to leave that to production. It no longer does,
and the claim was worth correcting rather than leaving: it is the sentence that
justified having no test for the two defects review found.
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

candidate_years = backfill.candidate_years
classify = backfill.classify


def match_year(value, history):
    """The single candidate, or None -- the shape the earlier tests assumed."""
    years = candidate_years(value, history)
    return years[0] if len(years) == 1 else None


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
    (34536.6555456551, None),     # the unrounded figure was never what we stored
    (34536.66, "2025"),           # as stored: round(34536.6555456551, 2)
    (34536.6, None),              # a different stored value entirely
    (34537.0, None),
])
def test_matching_reproduces_the_rounding_rather_than_tolerating_it(value, expected):
    """The ingester stored `round(float(v), 2)`, so the inverse is to round each
    candidate the same way -- not to allow a margin.

    A tolerance is not the inverse of a rounding. The first version used
    `max(abs(value) * 1e-6, 0.005)`, which at the largest stored value
    (114769.01) admits 0.115 -- twenty-three times what two decimal places can
    hide, and wide enough to take a neighbouring year with it.
    """
    assert match_year(value, HISTORY) == expected


def test_the_old_tolerance_would_have_admitted_a_neighbouring_year():
    """The concrete failure the rewrite removes.

    Two years half a unit apart at a magnitude where the old relative term was
    0.115: the tolerance matched both, so the value was called ambiguous when it
    is not, and at other magnitudes it would have matched the wrong one alone.
    """
    history = {"2024": 114769.01, "2023": 114769.06}
    assert candidate_years(114769.01, history) == ["2024"]

    old_tolerance = max(abs(114769.01) * 1e-6, 0.005)
    admitted = [y for y, v in history.items() if abs(v - 114769.01) <= old_tolerance]
    assert sorted(admitted) == ["2023", "2024"], "the old rule took both"


def test_zero_is_matched_rather_than_treated_as_missing():
    """Zero is a legitimate reading -- renewables share, for one -- and a
    relative tolerance collapses to nothing at zero, so the absolute floor is
    what carries it."""
    assert match_year(0.0, {"2024": 0.0, "2023": 5.0}) == "2024"


def test_a_negative_value_is_matched_on_magnitude():
    """Net balances go negative, and a tolerance derived from a signed value
    would be negative too, matching nothing at all."""
    assert match_year(-1234.56, {"2024": -1234.5612, "2023": 8.0}) == "2024"


# --- the verdicts, enumerated ------------------------------------------------
#
# The point of the rewrite is that every outcome short of a single candidate is
# recorded as itself. Collapsing them was the original defect at one remove: a
# rate limit, a truncated search and a genuine absence all became "lost".

RECOVERED = backfill.RECOVERED
AMBIGUOUS = backfill.AMBIGUOUS
NO_MATCH = backfill.NO_MATCH
OUTSIDE_WINDOW = backfill.OUTSIDE_WINDOW
UNAVAILABLE = backfill.UNAVAILABLE


def test_one_candidate_is_an_inference_not_a_quotation():
    """The status names what it is. The original response is gone, so this is
    inferred from a later one -- and an earlier draft called it `stated`, which
    would have put an unearned claim on forty-five thousand rows."""
    status, year, n = classify(34536.66, HISTORY, {})
    assert status == RECOVERED == "recovered_inferred"
    assert year == "2025"
    assert n == 1


def test_several_candidates_are_ambiguous_and_carry_their_count():
    status, year, n = classify(100.0, {"2024": 100.0, "2023": 100.0}, {})
    assert status == AMBIGUOUS
    assert year is None
    assert n == 2


def test_no_candidate_in_a_complete_answer_names_the_vintage():
    """Revision is one explanation and this does not choose between it and a
    changed dataset, indicator, unit or geography."""
    status, year, n = classify(35121.66, HISTORY, {"truncated": False})
    assert status == NO_MATCH == "no_match_current_vintage"
    assert (year, n) == (None, 0)


def test_an_incomplete_answer_yields_no_verdict_even_with_a_candidate():
    """Checked before the candidates, not after.

    The first version consulted the truncation flag only when it had found none,
    so a unique match on the part that was read became `recovered_inferred`
    while a second match sat unread beyond it -- a check that existed and could
    not fire in the one case that needed it.

    fetch_history no longer returns an incomplete answer at all; this pins the
    ordering so that a future caller who does cannot get a verdict out of it.
    """
    # A value that matches, in an answer known to be partial.
    status, year, n = classify(34536.66, HISTORY, {"incomplete": True})
    assert status == OUTSIDE_WINDOW == "outside_query_window"
    assert year is None, "a candidate from a partial answer must not be adopted"
    # `n` was bound and never checked. A partial answer that reported
    # `period_candidates = 1` would put a count on the row that reads as
    # evidence -- one candidate found, in a search that did not finish.
    assert n is None, "a partial answer must not report a candidate count"

    # And the same value in a complete one.
    assert classify(34536.66, HISTORY, {})[0] == RECOVERED


def test_no_answer_at_all_is_not_a_verdict():
    """A refusal recorded as a verdict retires the row from every future
    attempt. The first measurement of this data read 40.5% recoverable for
    exactly that reason -- 56 refused pairs counted as losses."""
    status, year, n = classify(1.0, None, {})
    assert status == UNAVAILABLE == "source_unavailable"
    assert (year, n) == (None, None)


def test_an_empty_but_successful_answer_is_not_confused_with_no_answer():
    """The source having nothing to say is evidence; the source not answering is
    not. They differ by one `is None`."""
    assert classify(1.0, {}, {"truncated": False})[0] == NO_MATCH
    assert classify(1.0, None, {})[0] == UNAVAILABLE


# --- the page parser, over the shapes a 200 can actually carry ---------------
#
# Explicit validation rather than exceptions caught after the fact. The version
# before this caught (ValueError, TypeError, KeyError, IndexError) around the
# parse, which left a real hole: a row that is a string rather than an object
# raises AttributeError from `r.get("value")`, so a malformed body still ended
# the whole run. Adding AttributeError to that list would have swallowed the
# same error coming from a mistake in the file, which is why the shape is
# checked instead of the failure caught.

MalformedResponse = backfill.MalformedResponse
parse_page = backfill.parse_page


def _page(rows, pages=1):
    import json
    return json.dumps([{"pages": pages, "lastupdated": "2026-07-01"}, rows]).encode()


def test_a_well_formed_page_parses():
    header, history, pages = parse_page(_page([{"date": "2025", "value": 34536.66}], pages=2))
    assert history == {"2025": 34536.66}
    assert pages == 2
    assert header["lastupdated"] == "2026-07-01"


def test_rows_with_no_value_are_skipped_not_refused():
    """The API returns a row per country-year whether or not it holds a figure,
    so absent values are the normal case, not a malformed response."""
    _, history, _ = parse_page(_page([{"date": "2025", "value": None},
                                      {"date": "2024", "value": 1.0}]))
    assert history == {"2024": 1.0}


@pytest.mark.parametrize("body,reason", [
    (b"<html>502 Bad Gateway</html>", "invalid_json"),
    (b"[]", "payload_not_two_items"),
    (b'{"message": "unavailable"}', "payload_not_two_items"),
    (b'["header-not-an-object", []]', "header_not_object"),
    (b'[{"pages": 1}, "rows-not-an-array"]', "rows_not_array"),
    (b'[{"pages": "many"}, []]', "pages_not_integer"),
    # The case that motivated the parser: a string where an object belongs.
    (b'[{"pages": 1}, ["not-a-dict"]]', "row_not_object"),
    (b'[{"pages": 1}, [{"value": 1.0}]]', "row_has_no_date"),
])
def test_a_response_that_is_not_what_the_api_documents_is_named(body, reason):
    with pytest.raises(MalformedResponse) as exc:
        parse_page(body)
    # The reason is machine-readable on purpose: a run report has to tell "the
    # source sent nonsense" from "the network was down", and those call for
    # different responses.
    assert str(exc.value) == reason


def test_a_programming_error_is_not_disguised_as_a_bad_response():
    """`except Exception` around the parse would have reported a mistake in this
    file as "the World Bank sent something unusable" -- deferred and retried for
    ever rather than fixed."""
    class Exploding:
        def decode(self):
            raise AttributeError("a mistake in this file")

    with pytest.raises(AttributeError):
        parse_page(Exploding())

