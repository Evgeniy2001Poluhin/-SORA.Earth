"""Which jobs run the moment the scheduler starts, and therefore on every deploy.

#154, found during the #121 production acceptance: recreating the scheduler
container ran the ingesters one second later, and nothing outside
app/scheduler.py said it would.

The first diagnosis was wrong and is worth recording, because it is the reason
this file measures rather than reasons. I assumed an APScheduler default --
that an interval trigger fires once at startup. It does not:

    IntervalTrigger(hours=24).get_next_fire_time(None, now)  ->  now + 24h

The behaviour is an explicit `modify_job(next_run_time=now)` over a named list,
with a stated reason. So the answer to "declare it or remove it" is declare: the
decision was made deliberately, and what was missing was its visibility.

These tests pin the membership. Adding a sixth job to that tuple has to be a
decision, not something that happens while editing nearby -- each entry costs a
database write or an external API call on every deployment, rollbacks included.
"""
import pytest

from app.scheduler import RUN_IMMEDIATELY_ON_STARTUP

EXPECTED = (
    "auto_run_ingesters",
    "auto_refresh_external_data",
    "refresh_forecast_metrics",
    "auto_openmeteo_ingestion",
    "auto_openmeteo_air_quality_ingestion",
)


def test_the_membership_is_exactly_this():
    """Named, not counted. A count of five passes on five wrong ids."""
    assert tuple(RUN_IMMEDIATELY_ON_STARTUP) == EXPECTED, (
        "the set of jobs that run on every deployment changed. Each one costs a "
        "write or an external call per release, including rollbacks, so this is "
        "a decision to make explicitly -- update EXPECTED and say why in the "
        "commit."
    )


def test_openaq_is_not_in_it():
    """Stated separately because its absence has its own reason.

    The job is not registered at all unless SORA_OPENAQ_ENABLED is set (#57),
    and forcing an immediate run of a job that does not exist would log a
    warning on every start.
    """
    assert "auto_openaq_ingestion" not in RUN_IMMEDIATELY_ON_STARTUP


def test_every_id_is_a_job_the_scheduler_registers(monkeypatch):
    """An id that matches nothing fails silently, once per start.

    `modify_job` raises for an unknown id and the loop logs a warning and moves
    on -- so a typo here is a job that never runs at startup and never says so
    louder than one line in a log nobody reads.

    On an isolated scheduler, not the module singleton. The first version
    removed every job from `app.scheduler.scheduler` and re-registered them,
    leaving the shared object in a state the next test inherits. It passed in
    the current order, which is the least useful kind of passing.

    `start=False` registers without starting, so nothing fires -- the docstring
    on init_scheduler says exactly why that argument exists. RUN_SCHEDULER is
    false in the suite, and init_scheduler returns before registering anything
    without it; the first version asserted against an empty set and was caught
    by its own denominator check.
    """
    from apscheduler.schedulers.background import BackgroundScheduler

    from app import scheduler as scheduler_module

    # The singleton as other tests left it. Compared before and after, so the
    # isolation claim is about *this* test rather than about the whole suite --
    # the first version asserted the module scheduler was empty at the end and
    # failed under the full run, because other files populate it legitimately.
    before = {job.id for job in scheduler_module.scheduler.get_jobs()}

    isolated = BackgroundScheduler(timezone="UTC")
    monkeypatch.setattr(scheduler_module, "scheduler", isolated)
    monkeypatch.setenv("RUN_SCHEDULER", "true")

    try:
        scheduler_module.init_scheduler(start=False)
        registered = {job.id for job in isolated.get_jobs()}
    finally:
        if isolated.running:
            isolated.shutdown(wait=False)

    monkeypatch.undo()
    after = {job.id for job in scheduler_module.scheduler.get_jobs()}

    assert after == before, (
        f"this test changed the module scheduler: added {sorted(after - before)}, "
        f"removed {sorted(before - after)}"
    )
    assert registered, "no jobs were registered, so this proves nothing"

    unknown = [
        jid for jid in RUN_IMMEDIATELY_ON_STARTUP
        if jid not in registered and jid != "auto_openaq_ingestion"
    ]
    assert unknown == [], (
        f"{unknown} are forced to run at startup but no job is registered under "
        f"those ids"
    )


def test_an_interval_trigger_does_not_fire_at_startup_by_itself():
    """The measurement that corrected the diagnosis.

    If a future APScheduler changes this, the forcing above becomes a duplicate
    rather than the cause, and the contract documented in app/scheduler.py stops
    describing what happens.
    """
    from datetime import datetime, timedelta, timezone

    from apscheduler.triggers.interval import IntervalTrigger

    now = datetime.now(timezone.utc)
    first = IntervalTrigger(hours=24).get_next_fire_time(None, now)

    assert first - now > timedelta(hours=23), (
        f"an interval job now fires {first - now} after start; the startup runs "
        f"are no longer explained by app/scheduler.py's modify_job loop"
    )


def test_the_contract_is_written_down_outside_the_code():
    """The whole point of #154: the behaviour existed and nothing said so."""
    import os

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    claude_md = open(os.path.join(repo_root, "CLAUDE.md")).read()

    assert "RUN_IMMEDIATELY_ON_STARTUP" in claude_md, (
        "CLAUDE.md does not mention the startup-run contract, which is how a "
        "deployment came to be an ingestion trigger that nobody had written down"
    )
