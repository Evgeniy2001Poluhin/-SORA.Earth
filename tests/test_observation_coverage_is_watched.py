"""Coverage of the M3 target is published, not just available on request.

`docs/WHERE_THE_PROJECT_STANDS.md` §3A names this as the highest-value work
while no forecasting result can be evidential: *"If accumulation breaks, the
date moves. A day under 80% coverage is a day the gate cannot use."* It then
says what would cover it -- "a per-day, per-point count against the 19-of-24
rule" -- and records that nothing watched for it.

The count now exists: `GET /api/v1/infra/observations/coverage`. Watching is a
separate claim, and it was not true. Measured 2026-09-19: no Grafana alert or
panel mentions coverage, `app/prom_metrics.py` declares no coverage gauge, and
nothing in `app/`, `web/src` or `scripts/` calls the endpoint. An admin endpoint
nobody calls reports a dip to nobody, and the §7 clock has been running since
2026-09-03.

What these tests pin is that the published numbers come from the endpoint's own
function rather than a second copy of its SQL, and that the count is never
published without the denominator that makes it readable.
"""
import pytest


def test_the_scheduler_registers_a_job_that_publishes_coverage(monkeypatch):
    """Registered, not merely defined.

    A function nobody schedules publishes nothing, which is the state this
    file exists to end. Asserted against an isolated scheduler so the module
    singleton other tests share is left as it was found.
    """
    from apscheduler.schedulers.background import BackgroundScheduler

    from app import scheduler as scheduler_module

    before = {job.id for job in scheduler_module.scheduler.get_jobs()}

    isolated = BackgroundScheduler(timezone="UTC")
    monkeypatch.setattr(scheduler_module, "scheduler", isolated)
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
    assert after == before, "this test changed the module scheduler"

    assert registered, "no jobs were registered, so this proves nothing"
    assert "auto_observation_coverage" in registered, (
        "no job publishes observation coverage, so a day below the 80% floor "
        "is visible only to someone who calls the admin endpoint by hand"
    )


def test_the_job_publishes_through_the_endpoints_own_function(monkeypatch):
    """One definition of the query, not two.

    The endpoint's SQL is the thing that decides whether a day counts -- it
    already had to be corrected once, from counting rows to counting hours. A
    job carrying its own copy would have kept the old answer while the endpoint
    reported the new one, and nothing would have disagreed out loud.
    """
    import app.api.infra as infra
    import app.scheduler as scheduler_module
    from app.schemas import ObservationCoverage

    called = {}

    def fake_report(source, indicator, days):
        called["args"] = (source, indicator, days)
        return ObservationCoverage(
            source=source, indicator=indicator, required_per_day=19,
            days_examined=16, points_examined=21, complete_days=13,
            gap_count=3, gaps=[],
        )

    monkeypatch.setattr(infra, "coverage_report", fake_report)

    result = scheduler_module.scheduled_observation_coverage()

    assert called, (
        "the job did not call app.api.infra.coverage_report; if it carries its "
        "own query, the published number and the served number can disagree"
    )
    assert result["gap_count"] == 3


def test_the_count_is_never_published_without_its_denominator(monkeypatch):
    """Zero gaps means "none fell short" or "nothing was looked at".

    Those are different facts about a deployment and they look identical in a
    single gauge. `ObservationCoverage` already reports the denominator beside
    the count for exactly this reason; publishing only the count would throw it
    away at the last step.
    """
    import app.api.infra as infra
    import app.prom_metrics as prom
    import app.scheduler as scheduler_module
    from app.schemas import ObservationCoverage

    monkeypatch.setattr(
        infra, "coverage_report",
        lambda source, indicator, days: ObservationCoverage(
            source=source, indicator=indicator, required_per_day=19,
            days_examined=0, points_examined=0, complete_days=0,
            gap_count=0, gaps=[],
        ),
    )

    published = {}
    for name in ("sora_observation_coverage_gap_days",
                 "sora_observation_coverage_days_examined",
                 "sora_observation_coverage_points_examined"):
        gauge = getattr(prom, name, None)
        assert gauge is not None, f"{name} is not declared in app/prom_metrics.py"

        class _Recorder:
            def __init__(self, key):
                self.key = key

            def labels(self, *args, **kwargs):
                return self

            def set(self, value):
                published[self.key] = value

        monkeypatch.setattr(prom, name, _Recorder(name))
        monkeypatch.setattr(scheduler_module, name, getattr(prom, name),
                            raising=False)

    scheduler_module.scheduled_observation_coverage()

    assert "sora_observation_coverage_gap_days" in published
    assert "sora_observation_coverage_days_examined" in published, (
        "the gap count was published without the number of days examined, so a "
        "reading of 0 cannot be told from a query that looked at nothing"
    )
    assert "sora_observation_coverage_points_examined" in published


@pytest.mark.parametrize("name", [
    "sora_observation_coverage_gap_days",
    "sora_observation_coverage_days_examined",
    "sora_observation_coverage_points_examined",
])
def test_each_gauge_declares_a_multiprocess_mode(name):
    """CLAUDE.md's rule, and it is load-bearing.

    A Gauge without one yields a series per process id under multiprocess mode,
    so an alert written against a single series silently matches several. These
    are written in the scheduler, which is one process today -- the mode is
    declared for the same reason the forecast gauges declare it: it is what an
    aggregated backend scrape would need if the writer ever moved.
    """
    import app.prom_metrics as prom

    gauge = getattr(prom, name, None)
    assert gauge is not None, f"{name} is not declared"
    assert getattr(gauge, "_multiprocess_mode", None) == "mostrecent", (
        f"{name} declares no multiprocess_mode"
    )
