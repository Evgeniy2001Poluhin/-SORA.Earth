"""`GET /api/v1/scheduler/status` must report the jobs that exist (#270).

`run_scheduler.py` published a hardcoded list of five jobs. There are
thirteen, and one of the five carried a trigger the code has never had:
`interval[12:00:00]` for `auto_refresh_external_data`, which is
`IntervalTrigger(hours=6)`. `get_scheduler_status()` returned that literal
tagged `source: "scheduler_container"`, and the operator's dashboard rendered
`jobs.length` -- so the panel said 5.

The same shape as the schedule section of CLAUDE.md before #252, and the same
figure: "every 12 hours" was removed from the document and left in the API.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _registered_jobs() -> dict[str, str]:
    """Every `scheduler.add_job(...)`, by id, with its trigger as written."""
    tree = ast.parse((ROOT / "app" / "scheduler.py").read_text())
    jobs = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "add_job":
            job_id = trigger = None
            for kw in node.keywords:
                if kw.arg == "id" and isinstance(kw.value, ast.Constant):
                    job_id = kw.value.value
                if kw.arg == "trigger":
                    trigger = ast.unparse(kw.value)
            if trigger is None and len(node.args) > 1:
                trigger = ast.unparse(node.args[1])
            if job_id:
                jobs[job_id] = trigger
    return jobs


def test_the_publisher_holds_no_job_list_of_its_own():
    """Derived from the scheduler, or it is a second list to keep in step.

    Asserted on the source because the alternative -- running the publisher --
    needs APScheduler, Redis and the whole application, and the property is
    about where the data comes from.
    """
    source = (ROOT / "run_scheduler.py").read_text()
    registered = _registered_jobs()

    assert len(registered) >= 13, f"only {len(registered)} jobs found; the scan broke"

    named = sorted(job_id for job_id in registered if f'"{job_id}"' in source)
    assert named == [], f"run_scheduler.py names jobs itself: {named}"

    tree = ast.parse(source)
    calls_get_jobs = any(
        isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "get_jobs"
        for node in ast.walk(tree)
    )
    assert calls_get_jobs, "the publisher does not ask the scheduler for its jobs"


def test_the_published_payload_carries_every_job_with_its_real_trigger(monkeypatch):
    """Run the publisher against a stand-in scheduler and read what it wrote."""
    import sys
    import types

    sys.path.insert(0, str(ROOT))

    class _Job:
        def __init__(self, jid, trigger, next_run):
            self.id = jid
            self.name = jid.replace("_", " ")
            self.trigger = trigger
            self.next_run_time = next_run

    import datetime as dt

    when = dt.datetime(2026, 9, 7, 3, 0, tzinfo=dt.timezone.utc)
    fake_jobs = [
        _Job("auto_closed_loop_daily", "cron[hour='3', minute='0']", when),
        _Job("auto_refresh_external_data", "interval[6:00:00]", when + dt.timedelta(hours=1)),
        _Job("never_scheduled", "interval[1:00:00]", None),
    ]

    written = {}

    class _Redis:
        @staticmethod
        def set(key, value, ex=None):
            written[key] = json.loads(value)

    import run_scheduler

    monkeypatch.setattr(run_scheduler, "scheduler",
                        types.SimpleNamespace(get_jobs=lambda: fake_jobs, running=True))
    monkeypatch.setitem(sys.modules, "app.redis_cache",
                        types.SimpleNamespace(redis_client=_Redis, REDIS_AVAILABLE=True))

    run_scheduler.publish_scheduler_status()

    payload = written["sora:scheduler:status"]
    assert payload["jobs_count"] == 3 == len(payload["jobs"])
    assert [j["id"] for j in payload["jobs"]] == [j.id for j in fake_jobs]
    assert payload["jobs"][1]["trigger"] == "interval[6:00:00]"
    assert payload["jobs"][0]["next_run"] == when.isoformat()
    assert payload["jobs"][2]["next_run"] is None, "a job with no next run must say so"


def test_running_comes_from_the_scheduler_and_is_not_asserted(monkeypatch):
    """`"running": True` was a literal, and a literal is always right.

    The stand-in reports **False** here. A payload that says True is repeating
    itself rather than reporting -- which is what it did, and what the first
    version of the test above could not see, because that stand-in happened to
    be running.
    """
    import sys
    import types

    sys.path.insert(0, str(ROOT))
    written = {}

    class _Redis:
        @staticmethod
        def set(key, value, ex=None):
            written[key] = json.loads(value)

    import run_scheduler

    monkeypatch.setattr(run_scheduler, "scheduler",
                        types.SimpleNamespace(get_jobs=lambda: [], running=False))
    monkeypatch.setitem(sys.modules, "app.redis_cache",
                        types.SimpleNamespace(redis_client=_Redis, REDIS_AVAILABLE=True))

    run_scheduler.publish_scheduler_status()

    payload = written["sora:scheduler:status"]
    assert payload["running"] is False, "the payload claims to be running when it is not"
    assert payload["jobs"] == []
    assert payload["jobs_count"] == 0


def test_the_snapshot_no_longer_takes_the_first_job():
    """`jobs[0]` is a wrong answer for twelve of the thirteen.

    The order matters and the list has none, so the test hands it a list whose
    first element is *not* the earliest -- which is precisely what the old code
    would have returned.
    """
    from app.api.admin_snapshot import earliest_next_run

    out_of_order = [
        {"id": "later", "next_run": "2026-09-07T05:00:00+00:00"},
        {"id": "sooner", "next_run": "2026-09-07T03:00:00+00:00"},
    ]

    assert earliest_next_run(out_of_order) == "2026-09-07T03:00:00+00:00"


@pytest.mark.parametrize("jobs, expected", [
    ([], None),
    ([{"next_run": None}], None),
    ([{"next_run": "2026-09-07T05:00:00+00:00"},
      {"next_run": "2026-09-07T03:00:00+00:00"}], "2026-09-07T03:00:00+00:00"),
    ([{"next_run": None},
      {"next_run": "2026-09-07T04:00:00+00:00"}], "2026-09-07T04:00:00+00:00"),
    (["not a dict", {"next_run": "2026-09-07T06:00:00+00:00"}],
     "2026-09-07T06:00:00+00:00"),
])
def test_the_earliest_run_across_the_shapes_that_arrive(jobs, expected):
    """The real function, not a copy of it. A test holding its own version of
    the logic stays right while the subject regresses."""
    from app.api.admin_snapshot import earliest_next_run

    assert earliest_next_run(jobs) == expected
