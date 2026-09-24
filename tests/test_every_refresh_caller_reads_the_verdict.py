"""Every caller of `refresh_live_data` reports the verdict the run recorded.

`refresh_live_data` labels its own run -- `success`, or `degraded` when a
source came back partial -- writes that label to its row in
`data_refresh_log`, and returns it as `status`. It returns it precisely
because callers had been guessing: #289 found the scheduler reading
`result.get("status", "success")` from a dictionary that had no such key.

#289 fixed the caller it named, and `POST /infra/data-refresh/run` was found
beside it by enumerating every construction of `DataRefreshLog`. That
enumeration covered who *writes the table*. Who *reads the function's answer*
is a different set, and nobody had counted it. Counted by AST on 2026-09-22:
five callers, two of which answered `success` for a degraded run whatever the
run recorded --

    app/api/admin_ai_control.py::ai_trigger_refresh   status="success", a constant
    app/agents/ai_teammate.py::_do_refresh           {"status": "success", ...}, a constant

The first is `POST /api/v1/admin/ai/refresh`, whose whole purpose is to tell an
AI agent what happened. The second is what `POST /admin/ai-teammate/run?mode=auto`
records as the result of the refresh it executed. In both, the row said
`degraded` and the answer beside it said `success`.

## How this is tested

The real `refresh_live_data`, over a scratch sqlite table, with only the
network and the locks replaced -- the fixture of `test_refresh_writes_one_row.py`,
for the reasons written there. Nothing here hands a caller a hand-written copy
of the function's answer. `tests/test_ai_teammate.py` did, with
`{"fetched": 32, "total": 32, "countries": []}`: a dictionary without the
`status` key, so the contract moved under that test and it stayed green.

Each caller is driven through both verdicts. A caller that answered `degraded`
unconditionally would pass the degraded run alone; the `success` run is the
control that shows it reads rather than asserts. Before each caller is judged,
the row the run wrote is checked to say the verdict under test -- otherwise a
fixture that failed to degrade the run would be measured instead of the caller.

The set of callers is pinned by name and each name has a driver below, so a
sixth caller fails `test_every_caller_is_driven` until a driver is written for
it -- which is the moment someone has to ask what it reports.
"""

from __future__ import annotations

import ast
import types
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

REPO = Path(__file__).resolve().parents[1]
TARGET = "refresh_live_data"
VERDICTS = ("success", "degraded")


def callers_of_refresh_live_data() -> set[str]:
    """Every `path::function` in `app/` that calls `refresh_live_data`."""
    found = set()
    for path in sorted((REPO / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None)
            if name != TARGET:
                continue
            enclosing = [
                f for f in functions
                if f.lineno <= node.lineno <= (f.end_lineno or f.lineno)
            ]
            innermost = max(enclosing, key=lambda f: f.lineno, default=None)
            label = innermost.name if innermost else "<module>"
            found.add(f"{path.relative_to(REPO)}::{label}")
    return found


class _RedisLock:
    """`RedisLock`, minus Redis: always acquired."""

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
    """The real function over a real table; `verdict(...)` picks the outcome."""
    import app.database as database
    import app.external_data as ed
    import app.locks as locks
    from app.database import Base, DataRefreshLog

    engine = create_engine(f"sqlite:///{tmp_path}/refresh.db")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # Both references, for the reason test_refresh_writes_one_row.py gives:
    # `app/external_data.py` binds `SessionLocal` at import.
    monkeypatch.setattr(ed, "SessionLocal", Session)
    monkeypatch.setattr(database, "SessionLocal", Session)
    monkeypatch.setattr(
        ed, "refresh_all_countries",
        lambda: {"fetched": 30, "total": 30, "countries": {}})
    monkeypatch.setattr(locks, "RedisLock", _RedisLock)

    def verdict(which: str) -> None:
        if which == "success":
            monkeypatch.setenv("SORA_HISTORY_REFRESH", "off")
            return
        assert which == "degraded", which
        # One transient pair failure out of ten: the run's own rule labels
        # that `degraded` (external_data.py, `pairs_failed_transient`).
        monkeypatch.setenv("SORA_HISTORY_REFRESH", "on")
        monkeypatch.setattr(
            ed, "refresh_indicator_history",
            lambda **kw: {
                "fetched": 1, "inserted": 0, "unchanged": 0, "revised": 0,
                "no_value": 0, "no_period": 0, "pairs_attempted": 10,
                "pairs_succeeded": 9, "pairs_empty": 0, "pairs_refused": 0,
                "pairs_failed_transient": 1,
            })
        monkeypatch.setattr(
            locks, "StrictLock",
            lambda **kw: types.SimpleNamespace(
                acquire=lambda: locks.ACQUIRED,
                release=lambda: None,
                lost=types.SimpleNamespace(is_set=lambda: False)))

    def recorded() -> list[str]:
        with Session() as s:
            return [r.status for r in
                    s.query(DataRefreshLog).order_by(DataRefreshLog.id).all()]

    return types.SimpleNamespace(verdict=verdict, recorded=recorded)


# ---- one driver per caller: run it, return the status it reports ------------

def _admin_ai_refresh(monkeypatch):
    from app.api.admin_ai_control import ai_trigger_refresh

    return ai_trigger_refresh(_admin={"sub": "admin"}).status


def _ai_teammate_refresh(monkeypatch):
    from app.agents.ai_teammate import AITeammate

    return AITeammate(mode="auto")._do_refresh()["status"]


def _infra_manual_refresh(monkeypatch):
    from app.api.infra import data_refresh_run

    # `status` here is the envelope ("the request was handled") by that
    # endpoint's own docstring; the run's verdict is `refresh_status`.
    return data_refresh_run()["refresh_status"]


def _scheduled_refresh(monkeypatch):
    from app.scheduler import scheduled_refresh_external_data

    return scheduled_refresh_external_data()["status"]


def _full_pipeline(monkeypatch):
    import app.scheduler as scheduler

    # The second step is the closed loop, which is not what is judged here.
    monkeypatch.setattr(
        scheduler, "closed_loop_retrain",
        lambda **kw: {"status": "skipped", "reason": "not under test"})
    return scheduler.full_pipeline_run(trigger_source="test")["refresh_result"]["status"]


DRIVERS = {
    "app/api/admin_ai_control.py::ai_trigger_refresh": _admin_ai_refresh,
    "app/agents/ai_teammate.py::_do_refresh": _ai_teammate_refresh,
    "app/api/infra.py::data_refresh_run": _infra_manual_refresh,
    "app/scheduler.py::scheduled_refresh_external_data": _scheduled_refresh,
    "app/scheduler.py::full_pipeline_run": _full_pipeline,
}


def test_the_scan_finds_the_callers_it_judges():
    """A scan that found nothing would make the pinning below vacuous."""
    found = callers_of_refresh_live_data()
    assert len(found) >= 3, (
        f"the AST scan found only {sorted(found)}; refresh_live_data has had "
        f"at least three callers since #289, so the scan is broken"
    )


def test_every_caller_is_driven():
    found = callers_of_refresh_live_data()
    assert found == set(DRIVERS), (
        "the callers of refresh_live_data are not the ones this file drives.\n"
        f"  found, no driver: {sorted(found - set(DRIVERS))}\n"
        f"  driver, no caller: {sorted(set(DRIVERS) - found)}\n"
        "A new caller needs a driver in DRIVERS, and the parametrized test "
        "below then checks that it reports the verdict the run recorded."
    )


@pytest.mark.parametrize("verdict", VERDICTS)
@pytest.mark.parametrize("caller", sorted(DRIVERS))
def test_the_caller_reports_the_verdict_the_run_recorded(
        caller, verdict, refresh, monkeypatch):
    refresh.verdict(verdict)

    reported = DRIVERS[caller](monkeypatch)
    rows = refresh.recorded()

    # The setup, checked first: the run itself must have recorded the verdict
    # under test, or what follows would be judging the fixture.
    assert rows == [verdict], (
        f"the run wrote rows {rows}, not one row saying {verdict!r}; the "
        f"fixture did not produce the outcome this case is about"
    )
    assert reported == verdict, (
        f"{caller} reported status={reported!r} for a run whose own row in "
        f"data_refresh_log says {verdict!r}"
    )
