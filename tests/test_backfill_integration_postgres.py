"""The real backfill script, run end to end against a real PostgreSQL.

The unit tests cover the matching rule. They cannot cover where the two review
findings actually lived: a selection that never reached the rows a recheck
exists for, and a date left behind when a verdict was withdrawn. Both are SQL
inside `main()`, and both are invisible to any test that does not run it.

The previous attempt at this file was a shell script that spoke to a database
but never invoked the script -- it re-typed the corrected SQL and asserted the
database obeyed it. That passes on the day the script regresses, because the
correct version lives in the test. Everything here goes through
`subprocess.run([sys.executable, scripts/backfill_indicator_periods.py, ...])`:
the real argument parsing, the real HTTP, the real UPDATE.

The source is a stub on localhost rather than the World Bank. Not for speed --
for control. `test_a_matching_value_on_a_later_page_makes_it_ambiguous` needs a
series where the same rounded value appears on two different pages, which is the
exact shape the single-page version got wrong and which no live indicator can be
relied on to hold.
"""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import text

from tests.postgres_scratch import (  # noqa: F401  (scratch_db is a fixture)
    REPO_ROOT,
    requires_postgres,
    scratch_db,
)

# The tracked script, unless tools/mutation_backfill_periods.sh points this at a
# deliberately broken copy. Same convention as tests/test_backup_local_daily.sh.
SCRIPT = os.environ.get(
    "SCRIPT_UNDER_TEST",
    os.path.join(REPO_ROOT, "scripts", "backfill_indicator_periods.py"),
)

ISO, INDICATOR = "SAU", "NY.GDP.PCAP.CD"
# Stored rounded, as app/external_data.py writes it. The source figure it came
# from carries full precision, which is the whole reason the rule reproduces
# `round(float(v), 2)` instead of comparing within a tolerance.
STORED_VALUE = 34536.66
SOURCE_FIGURE = 34536.6555456551
VINTAGE = "2026-07-01"


class Stub:
    """A World Bank shaped answer, switchable between runs.

    `pages` is a list of row-lists: one entry per page, and the header reports
    `pages` accordingly, so a two-entry list is a genuinely paged response.
    """

    def __init__(self):
        self.pages = [[]]
        self.unobtainable_from = None
        self.requested = []
        self.paths = []
        self.base = None

    def body(self, page):
        rows = self.pages[page - 1] if page - 1 < len(self.pages) else []
        header = {
            "page": page,
            "pages": len(self.pages),
            "per_page": 500,
            "total": sum(len(p) for p in self.pages),
            "lastupdated": VINTAGE,
        }
        return json.dumps([header, rows]).encode()


def _handler_for(stub):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802  (the base class names it)
            page = int(parse_qs(urlparse(self.path).query).get("page", ["1"])[0])
            stub.requested.append(page)
            stub.paths.append(self.path)
            if stub.unobtainable_from is not None and page >= stub.unobtainable_from:
                self.send_error(503, "stub: this page is unobtainable")
                return
            body = stub.body(page)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return Handler


@pytest.fixture
def stub():
    subject = Stub()
    # Port 0: the runner picks a free one. A fixed port collides with whatever
    # else the job is running and turns into an unrelated flake.
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(subject))
    subject.base = "http://127.0.0.1:%d" % server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield subject
    finally:
        server.shutdown()
        server.server_close()


def series(*pairs):
    """[(year, figure), ...] as the API returns them."""
    return [{"date": str(y), "value": v} for y, v in pairs]


def insert_row(engine, value=STORED_VALUE, source="world_bank"):
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO country_indicator_history "
                "  (country_iso3, indicator_code, source, value, fetched_at) "
                "VALUES (:iso, :ind, :src, :val, now()) RETURNING id"
            ),
            {"iso": ISO, "ind": INDICATOR, "src": source, "val": value},
        ).scalar()


def read_row(engine, row_id):
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT period_status, as_of_date, period_candidates, "
                "       period_rule_version, period_run_id, period_method, "
                "       period_source_vintage, period_response_sha256, "
                "       period_resolved_at "
                "  FROM country_indicator_history WHERE id = :id"
            ),
            {"id": row_id},
        ).mappings().one()
    return dict(row)


def run_backfill(url, stub, *args):
    """The script itself, with its own argument parsing and its own HTTP."""
    result = subprocess.run(
        [sys.executable, SCRIPT, *args],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "DATABASE_URL": url.render_as_string(hide_password=False),
            "SORA_WORLDBANK_API_BASE": stub.base,
        },
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@requires_postgres
def test_a_single_candidate_is_recovered_with_the_provenance_of_its_run(scratch_db, stub):
    engine, url = scratch_db
    row_id = insert_row(engine)
    stub.pages = [series((2025, SOURCE_FIGURE), (2024, 35527.786181866))]

    run_backfill(url, stub, "--apply")

    row = read_row(engine, row_id)
    assert row["period_status"] == "recovered_inferred"
    assert row["as_of_date"].year == 2025
    assert row["period_candidates"] == 1
    assert row["period_method"] == "value_match"
    # Not "it wrote something": each of these is what makes a wrong run findable
    # and reversible afterwards.
    assert row["period_source_vintage"] == VINTAGE
    assert row["period_response_sha256"]
    assert row["period_run_id"]
    assert row["period_resolved_at"] is not None


@requires_postgres
def test_the_request_narrows_the_series_in_no_way(scratch_db, stub):
    """No date bound, so there is no window for a value to fall outside of.

    An explicit `date=1960:2030` was there, and `classify` could only report
    `outside_query_window` when handed an answer already flagged incomplete --
    which a fully-read bounded response never is. A value from 1959 would have
    been recorded as `no_match_current_vintage`: an assertion that the source
    revised the figure away, when nobody had looked.

    Measured before removing it (2026-08-04, country=all, no filter, 17,490
    records per indicator): nothing outside the bound for any of the four
    indicators in the table. The bound cost nothing and is gone anyway -- that
    measurement would have to be repeated for every indicator added later, by
    someone who knew to.
    """
    engine, url = scratch_db
    insert_row(engine)
    stub.pages = [series((2025, SOURCE_FIGURE))]

    run_backfill(url, stub, "--apply")

    assert stub.paths, "the source was never asked"
    for path in stub.paths:
        query = parse_qs(urlparse(path).query)
        assert "date" not in query, f"the request narrowed the series: {path}"
        assert "mrv" not in query, f"the request asked for recent values only: {path}"


@requires_postgres
def test_nothing_is_written_without_apply(scratch_db, stub):
    """Dry by default. The flag is the only thing between a report and 90,403
    rows, so it is asserted rather than assumed."""
    engine, url = scratch_db
    row_id = insert_row(engine)
    stub.pages = [series((2025, SOURCE_FIGURE))]

    out = run_backfill(url, stub)

    assert read_row(engine, row_id)["period_status"] is None
    assert "nothing was written" in out


@requires_postgres
def test_a_matching_value_on_a_later_page_makes_it_ambiguous(scratch_db, stub):
    """The regression the review found, executed.

    Page one holds a single match. Page two holds another year with the same
    rounded value. Reading only the first page yields exactly one candidate, and
    the row is dated 2025 -- confidently, and wrongly.

    The truncation flag existed at the time and could not save it: `classify`
    consulted it only after finding no candidate, so the one case that needed
    the check was the one case that never reached it.
    """
    engine, url = scratch_db
    row_id = insert_row(engine)
    stub.pages = [
        series((2025, SOURCE_FIGURE), (2024, 35527.786181866)),
        series((2011, 34536.6612), (2010, 25243.5)),
    ]

    run_backfill(url, stub, "--apply")

    row = read_row(engine, row_id)
    assert row["period_status"] == "ambiguous"
    assert row["as_of_date"] is None
    assert row["period_candidates"] == 2
    assert stub.requested == [1, 2], "the second page was never asked for"


@requires_postgres
def test_a_newer_rule_withdraws_an_older_verdict_and_the_date_goes_with_it(scratch_db, stub):
    """recovered_inferred -> ambiguous -> recovered_inferred, through the script.

    Two defects lived in this sequence. The selection carried
    `AND as_of_date IS NULL`, and a recovered row has a date by definition, so a
    corrected rule could never reach the verdicts an older rule got wrong. And
    the write used `as_of_date = COALESCE(%s, as_of_date)`, so withdrawing a
    verdict kept the date the discredited verdict had produced.
    """
    engine, url = scratch_db
    row_id = insert_row(engine)

    stub.pages = [series((2025, SOURCE_FIGURE), (2024, 35527.786181866))]
    run_backfill(url, stub, "--apply")
    assert read_row(engine, row_id)["period_status"] == "recovered_inferred"

    # Stage it as the work of an older rule. Only the tag is rewritten -- the
    # verdict and the date are the ones the script itself just wrote.
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE country_indicator_history "
                "SET period_rule_version = 'value-match/1' WHERE id = :id"
            ),
            {"id": row_id},
        )

    # A second year with the same rounded value: the older verdict was wrong.
    stub.pages = [series((2025, SOURCE_FIGURE), (2016, 34536.664))]
    run_backfill(url, stub, "--apply", "--recheck-rule", "value-match/1")

    row = read_row(engine, row_id)
    assert row["period_status"] == "ambiguous", "the recheck never reached the row"
    assert row["as_of_date"] is None, "a withdrawn verdict kept its date"
    assert row["period_candidates"] == 2

    # And the reverse: the ambiguity resolves, and the row is decided again.
    stub.pages = [series((2016, 34536.664), (2024, 35527.786181866))]
    run_backfill(url, stub, "--apply", "--recheck-rule", "value-match/2")

    row = read_row(engine, row_id)
    assert row["period_status"] == "recovered_inferred"
    assert row["as_of_date"].year == 2016


@requires_postgres
def test_an_unobtainable_page_leaves_the_row_for_a_later_run(scratch_db, stub):
    """A refusal is not a verdict.

    Page one arrives and holds a single match; page two never does. Recording
    anything here -- even `no_match_current_vintage` -- would retire the row from
    every future attempt on the strength of an answer that was never complete.
    """
    engine, url = scratch_db
    row_id = insert_row(engine)
    stub.pages = [series((2025, SOURCE_FIGURE)), series((2011, 34536.6612))]
    stub.unobtainable_from = 2

    run_backfill(url, stub, "--apply")

    row = read_row(engine, row_id)
    assert row["period_status"] is None
    assert row["as_of_date"] is None


@requires_postgres
def test_a_source_that_publishes_no_period_is_recorded_as_such(scratch_db, stub):
    """Benchmarks and global averages are derived; there is no period to find,
    and the source is never asked."""
    engine, url = scratch_db
    row_id = insert_row(engine, source="benchmark")
    stub.pages = [series((2025, SOURCE_FIGURE))]

    run_backfill(url, stub, "--apply")

    row = read_row(engine, row_id)
    assert row["period_status"] == "period_not_applicable"
    assert row["as_of_date"] is None
    assert row["period_method"] == "source_publishes_none"
    assert stub.requested == [], "a derived value was looked up at the source"


@requires_postgres
def test_a_value_the_source_no_longer_reports_is_recorded_not_dated(scratch_db, stub):
    engine, url = scratch_db
    row_id = insert_row(engine)
    stub.pages = [series((2025, 99999.11), (2024, 88888.22))]

    run_backfill(url, stub, "--apply")

    row = read_row(engine, row_id)
    assert row["period_status"] == "no_match_current_vintage"
    assert row["as_of_date"] is None
    assert row["period_candidates"] == 0
