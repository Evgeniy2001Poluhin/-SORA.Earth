"""World Bank values must carry the period they describe.

`_fetch_wb_indicator` returned the number and dropped `entry["date"]` — the year
the source states the observation is for. Every one of the 87,005 rows in
country_indicator_history was written with `as_of_date=None` because of it, so
the database could say when a figure was downloaded and never what it measured.

That makes temporal work impossible rather than merely inconvenient: a
rolling-origin split needs the observation time, and a fetch timestamp makes
every row look like it was observed today. See issue #58.
"""

from datetime import datetime
import pytest
from unittest.mock import patch

import app.external_data as ed


WB_RESPONSE = [
    {"page": 1, "total": 3},
    [
        {"date": "2025", "value": 34536.6555456551},
        {"date": "2024", "value": 35527.786181866},
    ],
]


def test_the_period_comes_back_with_the_value():
    """The pair, not the number alone."""
    with patch.dict("os.environ", {"SORA_OFFLINE": "0"}), \
         patch("app.external_data.httpx.get") as get:
        get.return_value.json.return_value = WB_RESPONSE
        get.return_value.raise_for_status.return_value = None
        value, period = ed._fetch_wb_indicator_dated("SAU", "NY.GDP.PCAP.CD")

    assert value == 34536.66
    assert period == "2025", "the year the source stated was dropped"


def test_the_first_non_null_entry_wins_and_keeps_its_own_year():
    """The value and the period must come from the same entry.

    Taking the value from one row and the year from another would be worse than
    no year at all -- it would look right and be wrong.
    """
    response = [
        {"page": 1},
        [
            {"date": "2026", "value": None},      # newest, but empty
            {"date": "2025", "value": 34536.65},  # this pair must be returned
        ],
    ]
    with patch.dict("os.environ", {"SORA_OFFLINE": "0"}), \
         patch("app.external_data.httpx.get") as get:
        get.return_value.json.return_value = response
        get.return_value.raise_for_status.return_value = None
        value, period = ed._fetch_wb_indicator_dated("SAU", "NY.GDP.PCAP.CD")

    assert (value, period) == (34536.65, "2025")


def test_a_year_becomes_a_date():
    assert ed._period_to_date("2025") == datetime(2025, 1, 1)


def test_no_period_stays_no_date():
    """A benchmark or a global average has no period, and must not acquire one.

    Inventing a date would make a fallback indistinguishable from a dated
    observation, which is the confusion this whole change is about.
    """
    assert ed._period_to_date(None) is None
    assert ed._period_to_date("") is None


@pytest.mark.parametrize("period,expected", [
    ("2025",         datetime(2025, 1, 1)),   # the shape the source actually sends
    (" 2025 ",       datetime(2025, 1, 1)),   # stripped, then accepted
    ("2025-invalid", None),                   # starts with a year and is not one
    ("20250",        None),                   # five digits
    ("202",          None),                   # three
    ("not-a-year",   None),
    (object(),       None),
])
def test_each_period_produces_exactly_this_date(period, expected):
    """One expected value per case, not "either of these is fine".

    The previous version asserted `result is None or result == 2025-01-01`, which
    every parameter satisfies whichever way the code behaves -- so a regression
    turning "2025-invalid" into a date would have passed. A test that accepts
    both answers tests nothing.

    The defect it exists for: `str(period)[:4]` accepted anything beginning with
    four digits and produced a false observation date, which is worse than none
    because it looks answerable.
    """
    assert ed._period_to_date(period) == expected


def test_a_year_outside_the_calendar_is_refused():
    """Four digits is the format, not the whole check."""
    assert ed._period_to_date("0000") is None


def _forget_cached_country(name="Saudi Arabia"):
    """Drop any cached result so the next call really fetches.

    No try/except. An earlier version swallowed every exception here, which would
    have let a failed invalidation hand the next test the previous one's cached
    result -- it would then assert against data its patches never produced, and
    pass. Silencing a failure in the setup of a test is the same defect this
    repository has been removing from its ingestion code all week; it does not
    become acceptable because it is in a test.
    """
    ed.invalidate_cache(name)


def test_the_period_reaches_the_country_result():
    """End to end through the assembly, which is where it was being lost."""
    _forget_cached_country()
    with patch.dict("os.environ", {"SORA_OFFLINE": "0"}), \
         patch("app.external_data.httpx.get") as get:
        get.return_value.json.return_value = WB_RESPONSE
        get.return_value.raise_for_status.return_value = None
        data = ed.get_country_esg_realtime("Saudi Arabia")

    assert data is not None
    periods = data.get("indicator_periods")
    assert periods, "indicator_periods missing from the result entirely"
    dated = [k for k, v in periods.items() if v == "2025"]
    assert dated, "no indicator carried the period the source stated: %r" % periods


def test_only_world_bank_values_are_dated():
    """A source that states no period must not be given one.

    Every indicator here falls through to a benchmark, so every period must be
    None. If one came back dated, the period of some earlier World Bank fetch
    would be leaking onto an undated value.
    """
    _forget_cached_country()
    with patch.dict("os.environ", {"SORA_OFFLINE": "0"}), \
         patch("app.external_data._fetch_wb_indicator_dated", return_value=(None, None)), \
         patch("app.external_data._fetch_oecd_indicator", return_value=None):
        data = ed.get_country_esg_realtime("Saudi Arabia")

    if data is None:
        return  # no benchmark for this country; nothing to assert
    assert set(data.get("indicator_periods", {}).values()) <= {None}, (
        "an undated source acquired a period: %r" % data.get("indicator_periods"))
