"""The pretrain job must not hold a database connection while it trains.

It used to close its session and keep using it (#127):

    db = SessionLocal()
    try:
        rows = db.query(Evaluation)...
    finally:
        db.close()                      # closed

    for metric_name in (...):
        df = load_time_series(db, metric_name)   # used anyway
        ...
        db = SessionLocal()             # rebound, the leaked one is now lost

A closed SQLAlchemy Session does not raise on reuse -- it acquires a fresh
connection and opens a new transaction -- so three metrics left three
connections `idle in transaction` for the life of the process. Measured on
production: three, ages 1266/1284/1308 s after a deployment, and three more
aged ~50 h before it.

The `finally: db.close()` sitting immediately above the loop is what made it
invisible on reading. That is why these tests assert the *shape* -- read, then
train with nothing open, then write -- and not merely that a close exists.

The fake Session below raises on use after close, which the real one does not.
That is the point: it turns a silent leak into a failure.
"""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


class ClosedSessionUsed(AssertionError):
    """Raised where SQLAlchemy would silently open a new connection."""


class FakeSession:
    """Tracks its own lifecycle and refuses use after close."""

    def __init__(self, ledger, transactional=False):
        self._ledger = ledger
        self._transactional = transactional
        self.closed = False
        self.committed = False
        self.rolled_back = False
        self.added = []
        ledger.opened.append(self)

    # -- lifecycle ------------------------------------------------------
    def close(self):
        self.closed = True
        self._ledger.closed.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        # `SessionLocal.begin()` commits on a clean exit and rolls back on an
        # exception. A fake that only closes cannot tell those apart, so a
        # test asserting "the earlier log survives" would pass against code
        # that rolled it back.
        if self._transactional:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        self.close()
        return False

    def rollback(self):
        self.rolled_back = True
        for obj in self.added:
            if obj in self._ledger.written:
                self._ledger.written.remove(obj)

    # -- use ------------------------------------------------------------
    def _check(self):
        if self.closed:
            raise ClosedSessionUsed(
                "a closed Session was used; SQLAlchemy would have opened a "
                "fresh connection here and never closed it"
            )
        self._ledger.uses.append(self)

    def query(self, *a, **k):
        self._check()
        return self._ledger.query_result

    def add(self, obj):
        self._check()
        self.added.append(obj)
        self._ledger.written.append(obj)

    def commit(self):
        self._check()
        self.committed = True


class Ledger:
    def __init__(self, rows):
        self.opened, self.closed, self.uses, self.written = [], [], [], []
        q = MagicMock()
        q.order_by.return_value = q
        q.all.return_value = rows
        self.query_result = q

    @property
    def open_now(self):
        return [s for s in self.opened if not s.closed]


class FakeSessionLocal:
    """Stands in for the sessionmaker, including its `begin()` helper."""

    def __init__(self, ledger):
        self._ledger = ledger

    def __call__(self):
        return FakeSession(self._ledger)

    def begin(self):
        return FakeSession(self._ledger, transactional=True)


def _series(n=40):
    return pd.DataFrame({
        "ds": pd.date_range("2026-01-01", periods=n, freq="D"),
        "y": [50.0 + (i % 7) for i in range(n)],
    })


def _run(monkeypatch, *, rows=None, train_side_effect=None, series=None,
         ledger=None):
    """Drive the job with every external effect captured.

    `ledger` may be passed in so several runs share one, which is the only way
    to observe accumulation across runs -- a fresh ledger per run starts from
    zero and would report "no accumulation" against a job that leaked every
    time.
    """
    import app.database as database
    from app.services.forecasting import data_loader
    import app.scheduler as scheduler

    if ledger is None:
        ledger = Ledger(rows if rows is not None else [object()] * 50)
    monkeypatch.setattr(database, "SessionLocal", FakeSessionLocal(ledger))

    during_training = []

    def _load(db, metric):
        db.query()  # a read must happen on a live session
        return (series or {}).get(metric, _series())

    monkeypatch.setattr(data_loader, "load_time_series", _load)

    calls = {"train": 0}

    def _fit(self, df, target_col=None, **kw):
        calls["train"] += 1
        during_training.append(len(ledger.open_now))
        if train_side_effect:
            train_side_effect(calls["train"])

    forecaster = MagicMock()
    forecaster.fit = _fit.__get__(forecaster, type(forecaster))
    forecaster.predict.return_value = MagicMock(yhat=[1.0])

    lock = MagicMock()
    lock.acquire.return_value = True

    with patch("app.locks.RedisLock", return_value=lock), \
         patch("app.services.forecasting.ModelRegistry.get", return_value=forecaster), \
         patch("app.services.forecasting.forecast_cache.store_fitted_model"), \
         patch("app.prom_metrics.sora_forecast_mae", MagicMock()), \
         patch("app.prom_metrics.sora_forecast_rmse", MagicMock()), \
         patch("app.prom_metrics.sora_forecast_r2", MagicMock()), \
         patch("app.prom_metrics.sora_forecast_mape", MagicMock()):
        result = scheduler.scheduled_pretrain_forecast_models()

    return result, ledger, during_training


# --- the leak itself --------------------------------------------------------


def test_no_session_is_used_after_being_closed(monkeypatch):
    """The defect in one assertion.

    Under the old code `load_time_series` was called on a closed session; the
    fake raises there, so this fails before the fix and passes after.
    """
    result, ledger, _ = _run(monkeypatch)

    assert result.get("status") != "error", result
    for session in ledger.opened:
        assert session.closed, "a session was left open when the job returned"


def test_every_session_is_closed_when_the_job_returns(monkeypatch):
    _result, ledger, _ = _run(monkeypatch)

    assert ledger.opened, "no session was opened at all; the test proves nothing"
    assert ledger.open_now == [], (
        f"{len(ledger.open_now)} session(s) still open -- each is a connection "
        f"held out of the pool for the life of the process"
    )


def test_nothing_holds_a_session_while_a_model_trains(monkeypatch):
    """The reason one session for the whole function was rejected.

    It would fix the lost reference and hold a read transaction open across
    LSTM and Prophet training instead -- three permanent transactions traded
    for one very long one.
    """
    _result, _ledger, open_during_training = _run(monkeypatch)

    assert open_during_training, "training never ran; the assertion is vacuous"
    assert max(open_during_training) == 0, (
        f"a session was open during training (max {max(open_during_training)})"
    )


def test_all_series_are_read_before_training_starts(monkeypatch):
    """Read, then compute. Not read-compute-read-compute."""
    order = []

    import app.database as database
    from app.services.forecasting import data_loader
    import app.scheduler as scheduler

    ledger = Ledger([object()] * 50)
    monkeypatch.setattr(database, "SessionLocal", FakeSessionLocal(ledger))

    def _load(db, metric):
        db.query()
        order.append(("read", metric))
        return _series()

    monkeypatch.setattr(data_loader, "load_time_series", _load)

    forecaster = MagicMock()
    forecaster.fit = lambda *a, **k: order.append(("train", None))
    forecaster.predict.return_value = MagicMock(yhat=[1.0])
    lock = MagicMock(); lock.acquire.return_value = True

    with patch("app.locks.RedisLock", return_value=lock), \
         patch("app.services.forecasting.ModelRegistry.get", return_value=forecaster), \
         patch("app.services.forecasting.forecast_cache.store_fitted_model"), \
         patch("app.prom_metrics.sora_forecast_mae", MagicMock()), \
         patch("app.prom_metrics.sora_forecast_rmse", MagicMock()), \
         patch("app.prom_metrics.sora_forecast_r2", MagicMock()), \
         patch("app.prom_metrics.sora_forecast_mape", MagicMock()):
        scheduler.scheduled_pretrain_forecast_models()

    kinds = [k for k, _ in order]
    assert "read" in kinds and "train" in kinds, order
    assert kinds.index("train") > max(i for i, k in enumerate(kinds) if k == "read"), (
        f"a read happened after training began: {order}"
    )


# --- semantics that must not change ----------------------------------------


def test_each_metric_is_written_in_its_own_transaction(monkeypatch):
    """One write for all three would roll back earlier logs on a later failure."""
    _result, ledger, _ = _run(monkeypatch)

    writing = [s for s in ledger.opened if s.added]
    assert len(writing) >= 2, (
        f"only {len(writing)} session(s) wrote; the per-metric commit "
        f"semantics have been collapsed into one transaction"
    )
    for session in writing:
        assert len(session.added) == 1, "a session wrote more than one metric log"


def test_a_later_failure_keeps_the_earlier_logs(monkeypatch):
    """Today a third-model failure leaves the first two committed. It stays.

    Three-row series so `len(test_df) >= 3` is false and the validation fits
    are skipped: exactly one `fit` per metric, so "call two" is the second
    *metric* rather than the second fit of the first one. Without that the
    failure could land before any metric had been written, and the assertion
    would hold for the wrong reason.

    Asserting on the first record's `status` rather than on the list being
    non-empty, because the failure path writes a log too -- a `status="failed"`
    row would satisfy "something was written" while proving nothing.
    """
    def fail_on_second(call_number):
        if call_number == 2:
            raise RuntimeError("second model refused to fit")

    tiny = {m: _series(3) for m in ("score", "prob", "co2_reduction")}
    _result, ledger, _ = _run(
        monkeypatch, train_side_effect=fail_on_second, series=tiny
    )

    assert ledger.written, "the first metric's log was lost when the second failed"
    assert ledger.written[0].status == "success", (
        f"the first record is {ledger.written[0].status!r}; the successful "
        f"metric's log did not survive the later failure"
    )
    assert any(getattr(o, "status", None) == "failed" for o in ledger.written), (
        "the failing metric wrote no failure log, so the run is not the "
        "scenario this test names"
    )


def test_a_failure_mid_run_still_closes_every_session(monkeypatch):
    def fail_on_second(call_number):
        if call_number == 2:
            raise RuntimeError("second model refused to fit")

    _result, ledger, _ = _run(monkeypatch, train_side_effect=fail_on_second)

    assert ledger.open_now == [], (
        "a failure left a session open, which is the leak by another route"
    )


def test_repeated_runs_do_not_accumulate_open_sessions(monkeypatch):
    """Three per invocation was the observed cost.

    One ledger across all three runs: a fresh ledger per run starts from zero
    and would report "no accumulation" against a job that leaks every time.
    """
    shared = Ledger([object()] * 50)

    for run in range(1, 4):
        _result, _ledger, _ = _run(monkeypatch, ledger=shared)
        assert shared.open_now == [], (
            f"after run {run}, {len(shared.open_now)} session(s) are still "
            f"open; the leak accumulates across invocations"
        )

    assert len(shared.opened) >= 3 * run, (
        "the runs did not open sessions at all, so nothing was measured"
    )


# --- the shape, not just the outcome ---------------------------------------


def test_the_session_variable_is_not_rebound_inside_the_metric_loop():
    """What made the leak invisible on reading.

    A `finally: db.close()` sat immediately above the loop and appeared to
    cover it, while the loop used the closed session and then rebound the name.
    """
    import inspect

    from app import scheduler

    src = inspect.getsource(scheduler.scheduled_pretrain_forecast_models)
    body = src[src.index("for metric_name"):]

    assert "db = SessionLocal()" not in body, (
        "a session is bound to `db` inside the metric loop again"
    )
    assert "read_db" in src and "write_db" in src, (
        "the read and write sessions share a name, which is how the previous "
        "one hid"
    )
