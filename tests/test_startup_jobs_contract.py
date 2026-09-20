"""Which jobs run the moment the scheduler starts, and therefore on every deploy.

#154, found during the #121 production acceptance: recreating the scheduler
container ran the ingesters one second later, and nothing outside
app/scheduler.py said it would.

The first diagnosis was wrong and is worth recording, because it is the reason
this file measures rather than reasons. I assumed an APScheduler default --
that an interval trigger fires once at startup. It does not:

    IntervalTrigger(hours=24).get_next_fire_time(None, now)  ->  now + 24h

The behaviour is an explicit `modify_job(next_run_time=now)` over a named list.
That establishes the mechanism as deliberate. It does not establish that each
of the five members was chosen: archaeology found a written reason for exactly
one (#82, air quality), while the other four arrived with commit messages that
gave none. #156 closed that on 2026-09-19 -- all five stay and the four missing
reasons were supplied by decision, since the commits do not contain them.

So these tests pin the *membership* and, since #156, that every member states
why it is a member -- which is a claim about what is written down, not about
whether the reason is a good one. Adding a sixth entry
has to be a decision rather than something that happens while editing nearby --
each one costs a database write or an external API call on every deployment,
rollbacks included.
"""
import os
import re

import pytest

from app.scheduler import RUN_IMMEDIATELY_ON_STARTUP

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
    # Both gates, not just the one that bit locally. init_scheduler returns
    # early on either RUN_SCHEDULER != true or SORA_SCHEDULER != "1", and this
    # test inherited both from the process environment -- so under
    # SORA_SCHEDULER=0 it would fail on its own denominator assertion instead of
    # testing the contract it names.
    monkeypatch.setenv("RUN_SCHEDULER", "true")
    monkeypatch.setenv("SORA_SCHEDULER", "1")

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


def _startup_comment_blocks() -> dict:
    """The per-job comment block in app/scheduler.py, keyed by job id.

    Parsed rather than grepped, so a reason written for one job cannot satisfy
    the assertion for another -- the whole defect #156 recorded is that four
    entries were carried along by the tuple they sat in. A block is the lines
    from a `#   <job id>` heading to the next heading, inside the comment
    region that documents RUN_IMMEDIATELY_ON_STARTUP.
    """
    src = open(os.path.join(REPO_ROOT, "app", "scheduler.py")).read()
    start = src.index("# Jobs that run once at startup")
    end = src.index("RUN_IMMEDIATELY_ON_STARTUP = (")
    region = src[start:end]

    blocks: dict = {}
    current = None
    for line in region.splitlines():
        heading = re.fullmatch(r"#   (\S+)", line)
        if heading:
            current = heading.group(1)
            blocks[current] = []
        elif current is not None:
            blocks[current].append(line)
    return {jid: "\n".join(lines) for jid, lines in blocks.items()}


@pytest.mark.parametrize("job_id", EXPECTED)
def test_every_startup_job_states_why_it_runs_at_startup(job_id):
    """#156, resolved: keep all five, and write down why each one is there.

    Four of the five arrived with no stated reason -- a6d5ede added two under an
    empty commit body, #11 added two more. "It was already in the tuple" is not
    a reason, and the resolution of #156 was to supply the missing four rather
    than to remove them. This test is what stops one from being deleted again by
    the next edit that only reads the code.

    Asserted against the block for *this* job id, so a reason present for the
    one entry that always had one (#82, air quality) does not cover the others.
    """
    blocks = _startup_comment_blocks()
    assert job_id in blocks, (
        f"app/scheduler.py runs {job_id} on every deployment and its comment "
        f"region does not document it. Blocks found: {sorted(blocks)}"
    )

    block = blocks[job_id]
    match = re.search(r"why at startup:(.*?)(?=\n#     repeat:|\Z)", block, re.S)
    assert match, (
        f"the block for {job_id} has no 'why at startup:' line, so nothing says "
        f"why this job costs a write or an external call on every release"
    )

    reason = match.group(1)
    assert "NOT STATED" not in reason.upper(), (
        f"{job_id} runs on every deployment -- and on every rollback -- and its "
        f"reason is still recorded as NOT STATED. #156 resolved this by keeping "
        f"all five and writing the reason down; supply it or remove the job "
        f"from RUN_IMMEDIATELY_ON_STARTUP."
    )
    assert len(reason.split()) >= 8, (
        f"the reason for {job_id} is {reason.strip()!r}, which is too short to "
        f"be one: it has to say what the startup run buys, not that it exists"
    )


@pytest.mark.parametrize("job_id", EXPECTED)
def test_the_claude_md_table_states_a_reason_for_each_startup_job(job_id):
    """The table an operator reads, checked against the same resolution.

    CLAUDE.md carried "**not stated**" in the 'why at startup' column for the
    same four jobs. A reason written only in app/scheduler.py leaves the
    document that gets read during a deployment saying the opposite.
    """
    claude_md = open(os.path.join(REPO_ROOT, "CLAUDE.md")).read()

    # Scoped to the startup table by its header, not matched on the job id
    # anywhere in the file: four of these five also appear in the scheduler's
    # own jobs table, and the first version of this test read that row instead
    # -- it would have passed on a reason written in the wrong table.
    header = "| job | side effect | why at startup | repeating it |"
    assert header in claude_md, (
        "the CLAUDE.md startup table header changed; this test can no longer "
        "tell that table apart from the scheduler jobs table above it"
    )
    table = claude_md.split(header, 1)[1].split("\n\n", 1)[0]

    rows = [
        line for line in table.splitlines()
        if line.lstrip().startswith(f"| `{job_id}` |")
    ]
    assert len(rows) == 1, (
        f"expected exactly one CLAUDE.md startup-table row for {job_id}, "
        f"found {len(rows)}"
    )

    assert "not stated" not in rows[0].lower(), (
        f"CLAUDE.md still tells an operator that {job_id}'s startup run has no "
        f"stated reason. #156 supplied one -- regenerate the row from the "
        f"comment block in app/scheduler.py."
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
