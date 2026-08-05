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
        # httpx raises for anything outside 2xx, not just 4xx and 5xx. An
        # earlier version of this fake raised only at >= 400, so a 3xx slipped
        # past it into the envelope parser and the test that meant to exercise
        # the status branch was exercising something else.
        if not 200 <= self.status_code < 300:
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
    monkeypatch.setattr(ed.httpx, "get",
                        lambda url, timeout=None, follow_redirects=None: responder(url))


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


def test_a_partial_page_failure_keeps_rows_but_is_not_ok(monkeypatch):
    """Page 1 arrived, page 2 timed out.

    The rows already gathered are kept -- partial data beats none -- but the
    series is incomplete, so the pair is transient and the run containing it
    is degraded. An earlier version called this ok because observations
    arrived, which hid exactly the gap these counters exist to expose: a pair
    missing half its history would have been indistinguishable from one that
    fetched everything.
    """
    calls = {"n": 0}

    def flaky(url):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Resp(_envelope([_row("2024", 4.9)], pages=2, page=1))
        raise httpx.ReadTimeout("slow")

    _serve(monkeypatch, flaky)
    rows, outcome = ed._fetch_wb_series("RUS", "X")
    assert rows == [("2024", 4.9)], "the rows that did arrive were discarded"
    assert outcome == ed.FETCH_TRANSIENT, (
        "an incomplete series was reported as a complete one"
    )


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


def test_a_404_is_refused_not_transient(monkeypatch):
    """A 4xx that is not 429 is the source saying no, permanently.

    It reached the generic handler and was reported transient, which would
    have degraded every run forever over something no retry can fix -- the
    opposite of the distinction this module exists to draw.
    """
    def not_found(url):
        return _Resp([], status=404)

    _serve(monkeypatch, not_found)
    rows, outcome = ed._fetch_wb_series("RUS", "X")

    assert outcome == ed.FETCH_REFUSED
    assert rows == []


def test_a_429_is_still_transient(monkeypatch):
    """The neighbour it must not be confused with: rate limiting passes."""
    _serve(monkeypatch, lambda url: _Resp([], status=429))
    assert ed._fetch_wb_series("RUS", "X")[1] == ed.FETCH_TRANSIENT


def test_a_500_is_still_transient(monkeypatch):
    _serve(monkeypatch, lambda url: _Resp([], status=503))
    assert ed._fetch_wb_series("RUS", "X")[1] == ed.FETCH_TRANSIENT


def test_losing_the_lock_degrades_the_run(monkeypatch):
    """A run that stopped early must not record success.

    The pairs it never reached would otherwise be indistinguishable from
    pairs the source has nothing for -- which is the whole argument for these
    counters, applied to the case where there are no counters at all.
    """
    from unittest.mock import MagicMock

    captured = {}

    class _Log:
        def __setattr__(self, k, v):
            captured[k] = v

    def raise_lock_lost(**kw):
        raise ed.HistoryRefreshLockLost("the refresh lock was lost mid-run")

    monkeypatch.setattr(ed, "SessionLocal", lambda: MagicMock())
    monkeypatch.setattr(ed, "DataRefreshLog", lambda **kw: _Log())
    monkeypatch.setattr(ed, "refresh_all_countries",
                        lambda: {"fetched": 1, "total": 1, "countries": {}})
    monkeypatch.setattr(ed, "refresh_indicator_history", raise_lock_lost)
    monkeypatch.setenv("SORA_HISTORY_REFRESH", "on")

    import sys
    import types
    module = types.ModuleType("app.redis_cache")
    module.REDIS_AVAILABLE = True
    module.redis_client = MagicMock()
    module.redis_client.set.return_value = True
    monkeypatch.setitem(sys.modules, "app.redis_cache", module)

    ed.refresh_live_data(trigger_source="test")

    assert captured["status"] == "degraded", (
        "a run that lost its lock partway recorded success"
    )
    assert "lock lost" in captured["message"]


def test_a_302_is_transient_not_refused(monkeypatch):
    """A moved path is not a dead indicator.

    `raise_for_status` fires on anything outside 2xx, so a 3xx reached the
    refusal branch and would have been recorded as permanent -- the source
    moving a URL would have looked exactly like the indicator ceasing to
    exist, and no retry would ever have been made.

    Redirects are followed now, so a 3xx arriving here at all is unusual
    (a loop, or a hop the client declined) and is worth retrying.
    """
    _serve(monkeypatch, lambda url: _Resp([], status=302))
    assert ed._fetch_wb_series("RUS", "X")[1] == ed.FETCH_TRANSIENT


def test_redirects_are_followed_by_policy(monkeypatch):
    """Stated on the request rather than left to httpx's default of False."""
    seen = {}

    def record(url, timeout=None, follow_redirects=None):
        seen["follow_redirects"] = follow_redirects
        return _Resp(_envelope([_row("2024", 1.0)]))

    monkeypatch.setattr(ed.httpx, "get", record)
    ed._fetch_wb_series("RUS", "X")

    assert seen["follow_redirects"] is True, (
        "a moved path would surface as a 3xx instead of being followed"
    )


def test_a_418_is_still_refused(monkeypatch):
    """The 4xx boundary holds from the other side."""
    _serve(monkeypatch, lambda url: _Resp([], status=418))
    assert ed._fetch_wb_series("RUS", "X")[1] == ed.FETCH_REFUSED


def test_a_partial_pair_degrades_the_run(monkeypatch):
    """The consequence, asserted end to end rather than inferred.

    A pair that lost half its pages must not leave the run reporting success:
    the missing half is not distinguishable afterwards from history the source
    never had.
    """
    from unittest.mock import MagicMock

    calls = {"n": 0}

    def flaky(url):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Resp(_envelope([_row("2024", 4.9)], pages=2, page=1))
        raise httpx.ReadTimeout("slow")

    _serve(monkeypatch, flaky)

    db = MagicMock()
    db.get_bind.return_value.dialect.name = "sqlite"
    db.query.return_value.filter.return_value.order_by.return_value = []

    stats = ed.refresh_indicator_history(
        db=db, countries={"RUS": "RUS"}, indicators={"a": "AAA"})

    assert stats["pairs_succeeded"] == 0
    assert stats["pairs_failed_transient"] == 1
    assert stats["fetched"] == 1, "the row that did arrive was not kept"
