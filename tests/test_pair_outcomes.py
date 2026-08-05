"""Every (country, indicator) pair ends in exactly one recorded outcome.

Row counters cannot answer the question an automated schedule needs answered.
The production one-shot reported `fetched 9900, inserted 7225` — true, and
silent about the fact that 60 of its 210 pairs were refused outright by the
source, because two configured indicator codes do not exist (#97). Those 60
appeared only as warnings in a log, while the run recorded `success`.

So pairs are counted by outcome, and the three ways a pair can yield nothing
are kept apart:

    empty      the source has no observations here -- a fact about the world
    refused    unknown or archived indicator -- identical on every future run
    transient  timeouts and 5xx that outlived the retries -- likely to differ
               next time, and the only one that makes a run degraded

The distinction is not bookkeeping. `empty` and `refused` are permanent and
need no retry; `transient` means data may exist that this run did not collect,
and reporting that as success makes it indistinguishable from `empty`.

Related: #74, which is the same defect one level up -- a run's status
describing the whole while saying nothing about its parts.
"""

import httpx
import pytest

import app.external_data as ed


class _Resp:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status
        self.request = None

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error", request=None, response=self)

    def json(self):
        return self.payload


def _envelope(rows, pages=1, page=1):
    return [{"page": page, "pages": pages, "per_page": len(rows),
             "total": len(rows)}, rows]


def _row(date, value):
    return {"date": date, "value": value}


@pytest.fixture(autouse=True)
def offline_off(monkeypatch):
    monkeypatch.delenv("SORA_OFFLINE", raising=False)
    monkeypatch.setattr(ed, "_WB_BACKOFF", 0.01)


def _serve(monkeypatch, responder):
    monkeypatch.setattr(ed.httpx, "get", lambda url, timeout=None: responder(url))


def test_observations_are_ok(monkeypatch):
    _serve(monkeypatch, lambda url: _Resp(_envelope([_row("2024", 4.9)])))
    rows, outcome = ed._fetch_wb_series("RUS", "X")
    assert outcome == ed.FETCH_OK
    assert rows == [("2024", 4.9)]


def test_an_empty_envelope_is_empty_not_refused(monkeypatch):
    """The source simply has nothing for this pair. Not a fault, not a retry."""
    _serve(monkeypatch, lambda url: _Resp(_envelope([])))
    rows, outcome = ed._fetch_wb_series("RUS", "X")
    assert outcome == ed.FETCH_EMPTY
    assert rows == []


def test_an_error_envelope_is_refused(monkeypatch):
    """The dead-code case: HTTP 200 with a message and no data element.

    Permanent — every future run refuses identically — so it must not be
    counted as transient and retried forever, nor as empty and forgotten.
    """
    _serve(monkeypatch, lambda url: _Resp(
        [{"message": [{"id": "175", "value": "The indicator was not found."}]}]))
    rows, outcome = ed._fetch_wb_series("RUS", "GE.EST")
    assert outcome == ed.FETCH_REFUSED
    assert rows == []


def test_an_exhausted_retry_budget_is_transient(monkeypatch):
    def always_timeout(url):
        raise httpx.ReadTimeout("slow")

    _serve(monkeypatch, always_timeout)
    rows, outcome = ed._fetch_wb_series("RUS", "X")
    assert outcome == ed.FETCH_TRANSIENT
    assert rows == []


def test_a_partial_page_failure_keeps_what_it_got(monkeypatch):
    """Page 1 arrived, page 2 timed out.

    The pair produced observations, so it is not a failure to collect
    *anything* -- and the rows already gathered are stored rather than
    discarded. Reported as ok, with the give-up logged.
    """
    calls = {"n": 0}

    def flaky(url):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Resp(_envelope([_row("2024", 4.9)], pages=2, page=1))
        raise httpx.ReadTimeout("slow")

    _serve(monkeypatch, flaky)
    rows, outcome = ed._fetch_wb_series("RUS", "X")
    assert rows == [("2024", 4.9)]
    assert outcome == ed.FETCH_OK


def test_every_pair_lands_in_exactly_one_counter(monkeypatch):
    """The property that makes the counters trustworthy.

    Enumerated rather than sampled: if the four outcomes do not sum to
    attempted, some pair went unrecorded and the totals stop meaning anything.
    """
    from unittest.mock import MagicMock

    payloads = {
        "AAA": _envelope([_row("2024", 1.0)]),          # ok
        "BBB": _envelope([]),                            # empty
        "CCC": [{"message": [{"value": "not found"}]}],  # refused
    }

    def responder(url):
        for code, payload in payloads.items():
            if code in url:
                return _Resp(payload)
        raise httpx.ReadTimeout("slow")                  # transient

    _serve(monkeypatch, responder)

    db = MagicMock()
    db.get_bind.return_value.dialect.name = "sqlite"
    db.query.return_value.filter.return_value.order_by.return_value = []

    stats = ed.refresh_indicator_history(
        db=db,
        countries={"RUS": "RUS"},
        indicators={"a": "AAA", "b": "BBB", "c": "CCC", "d": "DDD"},
    )

    assert stats["pairs_attempted"] == 4
    assert stats["pairs_succeeded"] == 1
    assert stats["pairs_empty"] == 1
    assert stats["pairs_refused"] == 1
    assert stats["pairs_failed_transient"] == 1

    accounted = (stats["pairs_succeeded"] + stats["pairs_empty"]
                 + stats["pairs_refused"] + stats["pairs_failed_transient"])
    assert accounted == stats["pairs_attempted"], (
        f"{stats['pairs_attempted'] - accounted} pair(s) ended in no counter"
    )


def _run_scheduled(monkeypatch, history):
    """refresh_live_data with everything but the counters stubbed out."""
    from unittest.mock import MagicMock

    captured = {}

    class _Log:
        def __setattr__(self, k, v):
            captured[k] = v

    monkeypatch.setattr(ed, "SessionLocal", lambda: MagicMock())
    monkeypatch.setattr(ed, "DataRefreshLog", lambda **kw: _Log())
    monkeypatch.setattr(ed, "refresh_all_countries",
                        lambda: {"fetched": 1, "total": 1, "countries": {}})
    monkeypatch.setattr(ed, "refresh_indicator_history", lambda **kw: history)
    monkeypatch.setenv("SORA_HISTORY_REFRESH", "on")

    import sys
    import types
    module = types.ModuleType("app.redis_cache")
    module.REDIS_AVAILABLE = True
    module.redis_client = MagicMock()
    module.redis_client.set.return_value = True
    monkeypatch.setitem(sys.modules, "app.redis_cache", module)

    ed.refresh_live_data(trigger_source="test")
    return captured


def _counters(**over):
    base = {"fetched": 0, "inserted": 0, "unchanged": 0, "revised": 0,
            "no_value": 0, "no_period": 0, "pairs_attempted": 10,
            "pairs_succeeded": 10, "pairs_empty": 0, "pairs_refused": 0,
            "pairs_failed_transient": 0}
    base.update(over)
    return base


def test_a_transient_failure_makes_the_run_degraded(monkeypatch):
    """Not a warning in prose -- the status itself.

    A run reporting `success` while a pair it could not reach may hold data
    makes that pair indistinguishable from one the source has nothing for.
    """
    captured = _run_scheduled(monkeypatch, _counters(
        pairs_succeeded=9, pairs_failed_transient=1))

    assert captured["status"] == "degraded"
    assert "DEGRADED" in captured["message"]
    assert "1 transient" in captured["message"]


def test_refusals_alone_do_not_degrade_the_run(monkeypatch):
    """A refused pair is permanent, and known -- #97 tracks it.

    Degrading every run for it would make `degraded` the normal state and
    stop it meaning anything, which is the failure this whole line of work
    keeps finding.
    """
    captured = _run_scheduled(monkeypatch, _counters(
        pairs_succeeded=8, pairs_refused=2))

    assert captured["status"] == "success"
    assert "2 refused" in captured["message"]


def test_the_message_reports_pairs_not_only_rows(monkeypatch):
    captured = _run_scheduled(monkeypatch, _counters(
        pairs_succeeded=7, pairs_empty=2, pairs_refused=1))

    assert "pairs 7/10 ok" in captured["message"]
    assert "2 empty" in captured["message"]
