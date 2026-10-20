"""The M3 coverage alerts are provisioned, pinned to the scheduler, and read the right gauges.

What this cannot show is that Grafana fires them. It reads the provisioning file.
The behaviour under a real outage, recovery and a stopped scheduler is checked
after deployment, against the live Prometheus, and is recorded in the PR.
"""
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALERTS = os.path.join(REPO_ROOT, "grafana", "provisioning", "alerting", "alerts.yml")

GAP = "sora-observation-coverage-gap"
STALE = "sora-observation-coverage-stale"


@pytest.fixture(scope="module")
def rules():
    yaml = pytest.importorskip("yaml")
    with open(ALERTS, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    return {r["uid"]: r for g in doc["groups"] for r in g["rules"]}


def _expr(rules, uid):
    return rules[uid]["data"][0]["model"]["expr"]


def test_both_coverage_alerts_exist(rules):
    assert GAP in rules
    assert STALE in rules


def test_the_gap_rule_fires_on_any_missing_point_day(rules):
    """Strictly greater than zero: one missing point-day is the signal."""
    assert _expr(rules, GAP) == 'sora_observation_coverage_gap_days{job="sora-scheduler"} > 0'


def test_the_gap_rule_holds_for_thirty_minutes_as_a_warning(rules):
    assert rules[GAP]["for"] == "30m"
    assert rules[GAP]["labels"]["severity"] == "warning"


def test_the_stale_rule_reads_the_success_timestamp_not_the_gap_gauge(rules):
    """The gap gauge keeps its last value when a run fails, so it cannot notice
    a scheduler that has stopped. The timestamp is only advanced on success."""
    expr = _expr(rules, STALE)

    assert "sora_observation_coverage_last_success_timestamp_seconds" in expr
    assert "sora_observation_coverage_gap_days" not in expr


def test_the_stale_rule_covers_absence_and_age(rules):
    expr = _expr(rules, STALE)

    assert 'absent(sora_observation_coverage_last_success_timestamp_seconds{job="sora-scheduler"})' in expr
    assert 'time() - sora_observation_coverage_last_success_timestamp_seconds{job="sora-scheduler"} > 900' in expr


def test_the_stale_rule_holds_for_longer_than_a_restart_takes(rules):
    """After a restart the series is absent until the first 15-minute run. The hold
    must be longer than that, or every deployment pages."""
    assert rules[STALE]["for"] == "45m"
    assert rules[STALE]["labels"]["severity"] == "warning"


def test_both_rules_select_the_scheduler_job(rules):
    """Both processes export the same metric names. Without the job selector the
    backend's series would stand in for a stopped scheduler."""
    for uid in (GAP, STALE):
        expr = _expr(rules, uid)
        assert 'job="sora-scheduler"' in expr, uid
        assert 'job="sora-app"' not in expr, uid


def test_the_metrics_they_read_are_declared():
    with open(os.path.join(REPO_ROOT, "app", "prom_metrics.py"), encoding="utf-8") as fh:
        text = fh.read()

    assert "sora_observation_coverage_gap_days = Gauge(" in text
    assert "sora_observation_coverage_last_success_timestamp_seconds = Gauge(" in text


def test_the_scheduler_writes_the_success_timestamp():
    """A declared gauge nothing sets is a rule that can only ever be absent."""
    with open(os.path.join(REPO_ROOT, "app", "scheduler.py"), encoding="utf-8") as fh:
        text = fh.read()

    assert "sora_observation_coverage_last_success_timestamp_seconds.labels(" in text
