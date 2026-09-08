"""A refresh must leave one row, and that row must be the measured one (#289).

Every scheduled refresh wrote **two** rows to `data_refresh_log`. The first,
from `refresh_live_data`, was real: `started_at`, `source`, `trigger_source`,
the status it measured and 30 countries. The second came from the scheduler's
wrapper and was invented — it read `status`, `countries_fetched`,
`total_countries` and `message` out of the returned dictionary, and that
dictionary is `refresh_all_countries()`'s, which has never carried any of those
keys. So the wrapper's row got `0`, `0`, `None` and, from
`result.get("status", "success")`, always `success`.

Measured on production:

    external_data_refresh | success | manual         | 33
    external_data_refresh | success | auto_scheduler | 33

Exactly even, because each run wrote one of each. Three consequences, and the
third is the one this file is mostly about:

    double count      66 rows for 33 refreshes, in /admin/diagnostics and
                      /admin/snapshot, which count rows
    wrong trigger     the second row set no trigger_source, so the column
                      default `manual` filed 33 automatic runs as manual
    invented status   `refresh_live_data` honestly records `degraded` when a
                      source comes back partial; the wrapper's row said
                      `success` whatever happened, so a degraded refresh left
                      one honest row and one that could not say otherwise

**Why the existing tests were green.**
`test_every_metric_has_a_writer.py::test_the_refresh_records_the_outcome_it_reported`
covers exactly this metric and passes a stub returning `{"status": ...}` — a
key the real `refresh_live_data` has never returned. It proved the wrapper
handles a status *if given one* and never touched the question of whether one
arrives. The tests here run the real function against a real (sqlite) database
and count what is in the table afterwards.

The same duplicate shape lived in `POST /api/v1/infra/data-refresh/run`, where
the second row's status was not a bad default but the constant `"success"`.
That one is not in the issue: it was found by enumerating every construction of
`DataRefreshLog` instead of trusting the one the report named — which is why
the last test here pins the writer count rather than the two call sites.
"""

from __future__ import annotations

import ast
import types
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

REPO = Path(__file__).resolve().parents[1]


def _counter_value(counter: str, **labels) -> float:
    from prometheus_client import REGISTRY

    return REGISTRY.get_sample_value(counter, labels or None) or 0.0


class _Lock:
    """`RedisLock`, minus Redis. Acquired, released, nothing else."""

    def __init__(self, **kw):
        pass

    @staticmethod
    def acquire():
        return True

    @staticmethod
    def release():
        return None


@pytest.fixture()
def refresh(monkeypatch, tmp_path):
    """The real `refresh_live_data` over a real table, with only the network out.

    Nothing about the logging path is stubbed: the row is written by the code
    under test, through SQLAlchemy, into a database this test then reads. A
    double here would answer whatever it was built to answer, which is the
    failure the whole issue is made of.
    """
    import app.database as database
    import app.external_data as ed
    import app.locks as locks
    import app.scheduler as scheduler
    from app.database import Base, DataRefreshLog

    engine = create_engine(f"sqlite:///{tmp_path}/refresh.db")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # Both references. `app/external_data.py` binds `SessionLocal` at import,
    # and every wrapper that wrote a second row imported it from
    # `app.database` inside the call. Patching only the first one sent the
    # duplicate to whatever DATABASE_URL points at, so this table showed one
    # row and the test passed over the defect it exists to catch — found by
    # reinstating the bug and watching nothing go red.
    monkeypatch.setattr(ed, "SessionLocal", Session)
    monkeypatch.setattr(database, "SessionLocal", Session)
    monkeypatch.setattr(
        ed, "refresh_all_countries",
        lambda: {"fetched": 30, "total": 30, "countries": {}})
    monkeypatch.setenv("SORA_HISTORY_REFRESH", "off")

    # The attribute on the real module, not a replacement for the module.
    # Both functions import their lock *inside* the call, and swapping
    # `sys.modules["app.locks"]` for a namespace holding only `RedisLock`
    # made `from app.locks import ACQUIRED, HELD, StrictLock` raise — which
    # `refresh_live_data` catches, so the run recorded `error` and the test
    # was measuring an ImportError it had caused itself.
    monkeypatch.setattr(locks, "RedisLock", _Lock)

    def rows():
        with Session() as s:
            return s.query(DataRefreshLog).order_by(DataRefreshLog.id).all()

    return types.SimpleNamespace(
        ed=ed, locks=locks, scheduler=scheduler, rows=rows, session=Session)


def test_the_fixture_reaches_the_real_writer(refresh):
    """Negative control. Every assertion below counts rows in this table."""
    assert refresh.rows() == [], "the scratch table did not start empty"

    refresh.ed.refresh_live_data(trigger_source="control")
    written = refresh.rows()

    assert len(written) == 1, (
        f"refresh_live_data itself wrote {len(written)} row(s); the counts "
        f"below would be measuring the wrong thing"
    )
    assert written[0].started_at is not None, (
        "the row has no started_at, so this fixture is not exercising the "
        "writer the tests below are about"
    )


def test_a_scheduled_refresh_leaves_exactly_one_row(refresh):
    """The defect, stated as the issue states its acceptance criterion."""
    answer = refresh.scheduler.scheduled_refresh_external_data()
    written = refresh.rows()

    # The wrapper's own answer, checked before the table. It catches every
    # exception and returns `{"status": "error"}`, so a wrapper broken badly
    # enough to crash still leaves exactly one correct row behind — written by
    # the function it called before it fell over. Measured: removing the
    # verdict from refresh_live_data's return makes `result["status"]` raise,
    # and without this line every assertion below still passed.
    assert answer["status"] == "success", (
        f"the scheduled refresh returned {answer!r}; the row it wrote may "
        f"still be right, but the wrapper did not complete"
    )
    assert answer["countries_fetched"] == 30, answer

    assert len(written) == 1, (
        "a scheduled refresh wrote %d rows to data_refresh_log: %s. The "
        "wrapper is recording a second time over what refresh_live_data "
        "already wrote." % (
            len(written),
            [(r.trigger_source, r.status, r.countries_fetched) for r in written],
        )
    )

    row = written[0]
    assert row.trigger_source == "auto_scheduler", (
        f"the run was scheduled and the row says trigger_source="
        f"{row.trigger_source!r}. `manual` here is the column default, which "
        f"is what a row written without a trigger gets."
    )
    assert row.source == "world_bank_oecd", f"source={row.source!r}"
    assert row.started_at is not None, "no started_at"
    assert row.countries_fetched == 30, (
        f"countries_fetched={row.countries_fetched}; 0 of 0 is what the "
        f"invented row reported for every run"
    )
    assert row.status == "success"


def test_a_degraded_refresh_leaves_no_row_claiming_success(refresh, monkeypatch):
    """The consequence that matters: a report that cannot fail.

    While the source is healthy the duplicate is merely noise. On the day it
    degrades, the table holds one row saying `degraded` and one saying
    `success`, and every reader that counts `status='success'` finds a success
    that did not happen.
    """
    monkeypatch.setenv("SORA_HISTORY_REFRESH", "on")
    monkeypatch.setattr(
        refresh.ed, "refresh_indicator_history",
        lambda **kw: {
            "fetched": 1, "inserted": 0, "unchanged": 0, "revised": 0,
            "no_value": 0, "no_period": 0, "pairs_attempted": 10,
            "pairs_succeeded": 9, "pairs_empty": 0, "pairs_refused": 0,
            "pairs_failed_transient": 1,
        })

    monkeypatch.setattr(
        refresh.locks, "StrictLock",
        lambda **kw: types.SimpleNamespace(
            acquire=lambda: refresh.locks.ACQUIRED,
            release=lambda: None,
            lost=types.SimpleNamespace(is_set=lambda: False)))

    refresh.scheduler.scheduled_refresh_external_data()
    written = refresh.rows()

    assert [r.status for r in written] == ["degraded"], (
        "a refresh with a transient source failure left these rows: "
        + repr([(r.trigger_source, r.status) for r in written])
        + ". Any of them saying `success` is a success that did not happen."
    )


def test_a_degraded_refresh_is_not_counted_as_a_successful_one(refresh, monkeypatch):
    """The same lie, in Prometheus rather than in the table.

    The wrapper labelled the counter from its own row, and its own row said
    `success` unconditionally — so `sora_external_refresh_total{outcome=...}`
    could not report a degraded run either.
    """
    monkeypatch.setenv("SORA_HISTORY_REFRESH", "on")
    monkeypatch.setattr(
        refresh.ed, "refresh_indicator_history",
        lambda **kw: {
            "fetched": 1, "inserted": 0, "unchanged": 0, "revised": 0,
            "no_value": 0, "no_period": 0, "pairs_attempted": 10,
            "pairs_succeeded": 9, "pairs_empty": 0, "pairs_refused": 0,
            "pairs_failed_transient": 1,
        })

    monkeypatch.setattr(
        refresh.locks, "StrictLock",
        lambda **kw: types.SimpleNamespace(
            acquire=lambda: refresh.locks.ACQUIRED,
            release=lambda: None,
            lost=types.SimpleNamespace(is_set=lambda: False)))

    source = refresh.scheduler.EXTERNAL_REFRESH_SOURCE
    before_ok = _counter_value(
        "sora_external_refresh_total", source=source, outcome="success")
    before_bad = _counter_value(
        "sora_external_refresh_total", source=source, outcome="failed")

    refresh.scheduler.scheduled_refresh_external_data()

    after_ok = _counter_value(
        "sora_external_refresh_total", source=source, outcome="success")
    after_bad = _counter_value(
        "sora_external_refresh_total", source=source, outcome="failed")

    assert after_ok == before_ok, "a degraded refresh was counted as a success"
    assert after_bad - before_bad == 1.0, "the degraded refresh was not counted"


def test_a_failed_refresh_leaves_one_row_and_it_says_error(refresh, monkeypatch):
    """The error path was duplicated too, in both wrappers.

    `refresh_live_data` sets `status="error"` on its own row and re-raises, so
    the row exists before any caller sees the exception. A second row added by
    whoever catches it is the same double count with a different label.
    """
    def explode():
        raise RuntimeError("the source refused")

    monkeypatch.setattr(refresh.ed, "refresh_all_countries", explode)

    result = refresh.scheduler.scheduled_refresh_external_data()
    written = refresh.rows()

    assert len(written) == 1, (
        "a failed refresh wrote %d rows: %s"
        % (len(written), [(r.trigger_source, r.status) for r in written])
    )
    assert written[0].status == "error"
    assert result["status"] == "error"


def test_the_manual_endpoint_leaves_one_row_too(refresh):
    """`POST /infra/data-refresh/run`, the instance the issue does not name.

    Its second row hardcoded `status="success"` — not a default that happened
    to be wrong, a constant that could never be anything else.
    """
    from app.api.infra import data_refresh_run

    answer = data_refresh_run()
    written = refresh.rows()

    assert len(written) == 1, (
        "the manual refresh endpoint wrote %d rows: %s"
        % (len(written), [(r.trigger_source, r.status) for r in written])
    )
    assert written[0].trigger_source == "manual"
    assert answer["refresh_status"] == written[0].status, (
        "the endpoint reports a status the row does not carry"
    )


def test_only_one_module_writes_this_table():
    """The rule, rather than the two places that broke it.

    Both duplicates were wrappers around the one real writer, added by
    different authors at different times. Pinning the call sites would have
    caught neither before it was written; pinning the count catches the third.
    """
    writers = []
    for path in sorted((REPO / "app").rglob("*.py")):
        if path.name == "database.py":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "DataRefreshLog"
            ):
                writers.append(f"{path.relative_to(REPO)}:{node.lineno}")

    modules = sorted({w.split(":")[0] for w in writers})
    assert modules == ["app/external_data.py"], (
        "data_refresh_log is written from more than one module, and every "
        "duplicate so far has been a wrapper inventing a row over the one "
        "refresh_live_data already writes:\n  " + "\n  ".join(writers)
    )
    assert len(writers) == 1, (
        "one module, more than one construction: " + "\n  ".join(writers)
    )
