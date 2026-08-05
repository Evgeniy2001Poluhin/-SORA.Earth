"""World Bank ingestion must store the series, not the latest value repeatedly.

Issue #86, and the root cause behind #95. `_fetch_wb_indicator_dated` asks the
API for three observations (`mrv=3`) and returns the first non-null one,
discarding the rest. The refresh then writes that single value again on every
run, so production holds ~450 identical copies per (country, indicator) and
**one dated point per country** -- measured, across every indicator, not just
GDP growth.

A feature built on one point is a constant, and a constant regressor carries no
coefficient. So this is not "a column is missing data"; it is that the write
path cannot express a series at all.

These run against a real migrated PostgreSQL and a real HTTP server. The stub
serves World Bank-shaped JSON over localhost so the response parsing, the
pagination fields and the httpx call are all exercised -- a monkeypatched
`httpx.get` would return whatever the test handed it and prove nothing about
what the code does with a real payload.

The trigger added in #90 refuses any UPDATE of `value` or `fetched_at`, so a
revision cannot overwrite: it has to be a new row, and the point-in-time read
resolves which one was in force. That is asserted here rather than assumed.
"""

import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.postgres_scratch import (  # noqa: F401  (scratch_db is a fixture)
    requires_postgres,
    scratch_db,
)

pytestmark = requires_postgres

CODE = "NY.GDP.MKTP.KD.ZG"
ISO3 = "RUS"

# What the API returns, newest first, exactly as the World Bank orders it.
THREE_YEARS = [
    ("2025", 1.00),
    ("2024", 4.92),
    ("2023", 3.65),
]


class _Handler(BaseHTTPRequestHandler):
    """Serves one indicator series in the World Bank's two-element envelope."""

    series = THREE_YEARS

    def do_GET(self):  # noqa: N802  (BaseHTTPRequestHandler's spelling)
        rows = [
            {
                "indicator": {"id": CODE, "value": "GDP growth (annual %)"},
                "country": {"id": "RU", "value": "Russian Federation"},
                "countryiso3code": ISO3,
                "date": period,
                "value": value,
                "unit": "",
                "obs_status": "",
                "decimal": 1,
            }
            for period, value in type(self).series
        ]
        header = {
            "page": 1,
            "pages": 1,
            "per_page": len(rows),
            "total": len(rows),
            "sourceid": "2",
            "lastupdated": "2026-07-01",
        }
        body = json.dumps([header, rows]).encode()

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # keep pytest output readable


@pytest.fixture
def wb_stub(monkeypatch):
    """A real HTTP server standing in for api.worldbank.org."""
    import app.external_data as ed

    _Handler.series = THREE_YEARS
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    host, port = server.server_address
    monkeypatch.setattr(ed, "WB_BASE", f"http://{host}:{port}/v2")
    # The fallback chain short-circuits to GLOBAL_AVG when offline, which would
    # bypass the fetch this test exists to exercise.
    monkeypatch.delenv("SORA_OFFLINE", raising=False)

    try:
        yield _Handler
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def db(scratch_db):
    engine, _ = scratch_db
    with Session(engine) as session:
        yield session


def _stored(db):
    """(period, value) rows for the indicator, oldest period first."""
    return db.execute(text(
        "SELECT as_of_date, value FROM country_indicator_history "
        " WHERE country_iso3 = :iso3 AND indicator_code = :code "
        " ORDER BY as_of_date, id"
    ), {"iso3": ISO3, "code": CODE}).all()


def test_three_years_from_the_api_become_three_stored_periods(db, wb_stub):
    """The whole issue in one assertion.

    The API returns three annual observations; the database must hold three
    periods, not the newest one.
    """
    from app.external_data import refresh_indicator_history

    refresh_indicator_history(db=db, countries={ISO3: ISO3}, indicators={"gdp_growth": CODE})

    rows = _stored(db)
    assert len(rows) == 3, f"stored {len(rows)} row(s), expected the whole series"

    years = [r[0].year for r in rows]
    assert years == [2023, 2024, 2025]
    assert [float(r[1]) for r in rows] == pytest.approx([3.65, 4.92, 1.00])


def test_a_second_run_stores_nothing_new(db, wb_stub):
    """Idempotent: the same series re-fetched must not grow the table.

    Today's refresh appends the same value on every run -- ~450 identical
    copies per key in production.
    """
    from app.external_data import refresh_indicator_history

    first = refresh_indicator_history(db=db, countries={ISO3: ISO3},
                                      indicators={"gdp_growth": CODE})
    second = refresh_indicator_history(db=db, countries={ISO3: ISO3},
                                       indicators={"gdp_growth": CODE})

    assert first["inserted"] == 3
    assert second["inserted"] == 0
    assert second["unchanged"] == 3
    assert len(_stored(db)) == 3


def test_no_duplicate_natural_keys(db, wb_stub):
    """One row per (country, indicator, period) while nothing has been revised."""
    from app.external_data import refresh_indicator_history

    refresh_indicator_history(db=db, countries={ISO3: ISO3}, indicators={"gdp_growth": CODE})
    refresh_indicator_history(db=db, countries={ISO3: ISO3}, indicators={"gdp_growth": CODE})

    dupes = db.execute(text(
        "SELECT count(*) FROM ("
        "  SELECT country_iso3, indicator_code, as_of_date"
        "    FROM country_indicator_history"
        "   GROUP BY 1,2,3 HAVING count(*) > 1) d"
    )).scalar()
    assert dupes == 0


def test_a_revised_period_is_appended_not_overwritten(db, wb_stub):
    """The source restating a year must not destroy what we held before.

    #90's trigger refuses to rewrite `value`, so an implementation that tried
    to UPDATE would raise rather than silently rewrite history. This asserts
    the intended behaviour: a second row, and both readable.
    """
    from app.external_data import refresh_indicator_history
    from app.services.point_in_time import series_as_of

    refresh_indicator_history(db=db, countries={ISO3: ISO3}, indicators={"gdp_growth": CODE})
    before = datetime.utcnow()

    wb_stub.series = [("2025", 1.00), ("2024", 5.30), ("2023", 3.65)]  # 2024 restated
    stats = refresh_indicator_history(db=db, countries={ISO3: ISO3},
                                      indicators={"gdp_growth": CODE})

    assert stats["revised"] == 1
    assert stats["inserted"] == 0
    assert stats["unchanged"] == 2

    rows = db.execute(text(
        "SELECT value FROM country_indicator_history "
        " WHERE country_iso3 = :iso3 AND indicator_code = :code "
        "   AND as_of_date = '2024-01-01' ORDER BY fetched_at"
    ), {"iso3": ISO3, "code": CODE}).all()
    assert [float(r[0]) for r in rows] == pytest.approx([4.92, 5.30]), (
        "the earlier value was not kept alongside the revision"
    )

    # And the point-in-time read still answers for the moment before it --
    # through series_as_of, which is period-aware.
    #
    # Not value_as_of: it returns the most recently fetched value for the
    # indicator regardless of period, and a whole series is written under one
    # fetched_at, so the winner is whichever row the API happened to list last.
    # That was a sound reading while the table held one point per country; it
    # stops being one now that it holds series. Noted in the PR.
    assert series_as_of(db, ISO3, CODE, before)[datetime(2024, 1, 1)] == pytest.approx(4.92)
    assert series_as_of(db, ISO3, CODE, datetime.utcnow())[datetime(2024, 1, 1)] == pytest.approx(5.30)


def test_the_run_reports_what_it_did(db, wb_stub):
    """fetched / inserted / unchanged / revised / rejected, each meaning one thing.

    A run that says only "success" is how OpenAQ reported 408 empty runs as
    fine (#56).
    """
    from app.external_data import refresh_indicator_history

    stats = refresh_indicator_history(db=db, countries={ISO3: ISO3},
                                      indicators={"gdp_growth": CODE})

    assert set(stats) >= {"fetched", "inserted", "unchanged", "revised",
                          "no_value", "no_period"}
    assert stats["fetched"] == 3
    assert stats["inserted"] == 3
    assert stats["unchanged"] == 0
    assert stats["revised"] == 0
    assert stats["no_value"] == 0
    assert stats["no_period"] == 0


def test_an_observation_without_a_period_is_rejected_not_stored(db, wb_stub):
    """A value with no year cannot join a series and must not be invented one.

    #58 established that undated rows are unusable downstream; storing more of
    them would undo that work.
    """
    from app.external_data import refresh_indicator_history

    wb_stub.series = [("2025", 1.00), (None, 9.99)]
    stats = refresh_indicator_history(db=db, countries={ISO3: ISO3},
                                      indicators={"gdp_growth": CODE})

    assert stats["no_period"] == 1
    assert stats["inserted"] == 1
    assert len(_stored(db)) == 1


def _concurrent_refresh(engine, barrier, results, index):
    """One refresh in its own session, released together with the others."""
    from app.external_data import refresh_indicator_history

    with Session(engine) as session:
        barrier.wait(timeout=30)
        try:
            results[index] = refresh_indicator_history(
                db=session, countries={ISO3: ISO3},
                indicators={"gdp_growth": CODE})
        except Exception as e:  # recorded, so the assertion can name it
            results[index] = e


def test_two_concurrent_runs_insert_one_row_each_period(scratch_db, wb_stub):
    """Read -> classify -> insert is a check-then-act.

    Without serialisation both processes find the period absent and both
    insert it, so the same observation is stored twice and the table gains a
    duplicate that no constraint forbids. The Redis lock does not help here:
    it says who may *start*, and these two both started.

    A transaction-scoped advisory lock on (country_iso3, indicator_code) is
    what makes the second one see the first one's rows.
    """
    engine, _ = scratch_db
    barrier = threading.Barrier(2)
    results = [None, None]

    threads = [
        threading.Thread(target=_concurrent_refresh,
                         args=(engine, barrier, results, i))
        for i in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    for r in results:
        assert not isinstance(r, Exception), f"a run raised: {r!r}"

    inserted = sorted(r["inserted"] for r in results)
    unchanged = sorted(r["unchanged"] for r in results)
    assert inserted == [0, 3], f"expected one run to insert all three: {inserted}"
    assert unchanged == [0, 3], f"expected the other to see them: {unchanged}"

    with Session(engine) as check:
        rows = check.execute(text(
            "SELECT as_of_date, count(*) FROM country_indicator_history "
            " WHERE country_iso3 = :iso3 AND indicator_code = :code "
            " GROUP BY 1 ORDER BY 1"
        ), {"iso3": ISO3, "code": CODE}).all()

    assert [r[1] for r in rows] == [1, 1, 1], (
        f"a period was stored more than once: {rows}"
    )


def test_two_concurrent_runs_record_one_revision(scratch_db, wb_stub):
    """The same invariant on the revision path: one new version, not two.

    Weaker than the test above, and worth saying so. Removing the advisory
    lock makes that one fail deterministically ([3, 3] instead of [0, 3]);
    this one still passed without it, because the two threads happened not to
    overlap -- each finished its pair before the other read. So it is a
    regression guard on the end state, not a demonstration of the mechanism.

    Kept because the end state is what matters -- two identical revisions
    would make `series_as_of` resolve between duplicates and the history claim
    the source restated a year twice -- but the mechanism is proven by
    test_two_concurrent_runs_insert_one_row_each_period, not by this.
    """
    from app.external_data import refresh_indicator_history

    engine, _ = scratch_db
    with Session(engine) as setup:
        refresh_indicator_history(db=setup, countries={ISO3: ISO3},
                                  indicators={"gdp_growth": CODE})

    wb_stub.series = [("2025", 1.00), ("2024", 5.30), ("2023", 3.65)]

    barrier = threading.Barrier(2)
    results = [None, None]
    threads = [
        threading.Thread(target=_concurrent_refresh,
                         args=(engine, barrier, results, i))
        for i in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    for r in results:
        assert not isinstance(r, Exception), f"a run raised: {r!r}"

    assert sorted(r["revised"] for r in results) == [0, 1], (
        f"the revision was recorded twice: {[r['revised'] for r in results]}"
    )

    with Session(engine) as check:
        count = check.execute(text(
            "SELECT count(*) FROM country_indicator_history "
            " WHERE country_iso3 = :iso3 AND indicator_code = :code "
            "   AND as_of_date = '2024-01-01'"
        ), {"iso3": ISO3, "code": CODE}).scalar()

    assert count == 2, f"expected the original and one revision, got {count}"


def test_an_error_envelope_is_reported_not_swallowed(caplog):
    """A 200 with no data is two different things, and it used to be one.

    Found in rehearsal: 5 of 30 countries lost their whole GDP-growth series
    with no message at all, while the same request served 66 observations
    minutes later. The World Bank signals an error as a ONE-element list whose
    member holds `message` -- with HTTP 200, so raise_for_status never sees it.
    """
    import logging

    import app.external_data as ed

    class _Resp:
        status_code = 200
        request = None

        def raise_for_status(self):
            pass

        def json(self):
            return [{"message": [{"id": "175", "key": "Invalid format",
                                  "value": "The indicator was not found."}]}]

    original = ed.httpx.get
    ed.httpx.get = lambda url, timeout=None: _Resp()
    try:
        with caplog.at_level(logging.WARNING, logger="sora"):
            assert ed._fetch_wb_series("RUS", "DEAD.CODE") == []
    finally:
        ed.httpx.get = original

    # caplog.text, not record.message: the latter only exists once the record
    # has been formatted, so building the assertion from it fails on a record
    # that was logged perfectly well.
    assert "no data envelope" in caplog.text, (
        "the source said the indicator does not exist and nothing was logged"
    )
    assert "not found" in caplog.text, "the source's own message was dropped"


def test_an_empty_series_is_not_an_error(caplog):
    """And the other half: a real two-element envelope with no rows.

    A country the source simply has no observations for must not raise the
    alarm that a dead indicator code does, or the warning becomes noise.
    """
    import logging

    import app.external_data as ed

    class _Resp:
        status_code = 200
        request = None

        def raise_for_status(self):
            pass

        def json(self):
            return [{"page": 1, "pages": 1, "total": 0}, []]

    original = ed.httpx.get
    ed.httpx.get = lambda url, timeout=None: _Resp()
    try:
        with caplog.at_level(logging.WARNING, logger="sora"):
            assert ed._fetch_wb_series("RUS", "X") == []
    finally:
        ed.httpx.get = original

    assert "no data envelope" not in caplog.text


def test_the_feature_frame_actually_varies(db, wb_stub, monkeypatch):
    """The acceptance case: coverage, distinct values and variance, not row counts.

    A fuller table is not the goal -- a regressor that still resolves to one
    number per frame carries no coefficient however many rows back it. So this
    asserts on the frame the model receives.
    """
    import pandas as pd

    import app.database as database
    from app.external_data import refresh_indicator_history
    from app.services.forecasting.features import FeatureEngineer

    refresh_indicator_history(db=db, countries={ISO3: ISO3}, indicators={"gdp_growth": CODE})

    # The feature builder opens its own session.
    monkeypatch.setattr(database, "SessionLocal", lambda: db)

    frame = pd.DataFrame({
        "ds": pd.date_range(start="2023-06-01", periods=36, freq="ME"),
        "y": range(36),
    })
    result = FeatureEngineer().add_external_regressors(frame, country=ISO3)

    coverage = result["gdp_growth"].notna().mean()
    distinct = result["gdp_growth"].nunique()
    variance = result["gdp_growth"].var()

    assert coverage > 0
    assert distinct > 1, f"still one value across the frame: {distinct}"
    assert variance > 0, f"no temporal variance: {variance}"


def test_a_daily_frame_inside_one_year_is_still_flat(db, wb_stub, monkeypatch):
    """And the limit of the fix, asserted rather than left to be discovered.

    Annual observations merged onto a horizon shorter than a year resolve to a
    single value: correct, and still uninformative. Storing the series is
    necessary for variance, not sufficient -- the training window has to span
    more than one annual boundary for this regressor to carry anything.
    """
    import pandas as pd

    import app.database as database
    from app.external_data import refresh_indicator_history
    from app.services.forecasting.features import FeatureEngineer

    refresh_indicator_history(db=db, countries={ISO3: ISO3}, indicators={"gdp_growth": CODE})
    monkeypatch.setattr(database, "SessionLocal", lambda: db)

    frame = pd.DataFrame({
        "ds": pd.date_range(start="2026-01-01", periods=100, freq="D"),
        "y": range(100),
    })
    result = FeatureEngineer().add_external_regressors(frame, country=ISO3)

    assert result["gdp_growth"].nunique() == 1
    assert result["gdp_growth"].var() == 0


def test_a_null_value_is_rejected(db, wb_stub):
    """The World Bank pads a series with null years; those are not observations."""
    from app.external_data import refresh_indicator_history

    wb_stub.series = [("2025", None), ("2024", 4.92)]
    stats = refresh_indicator_history(db=db, countries={ISO3: ISO3},
                                      indicators={"gdp_growth": CODE})

    assert stats["no_value"] == 1
    assert stats["no_period"] == 0, "a null value is not a missing period"
    assert stats["inserted"] == 1
    assert len(_stored(db)) == 1
