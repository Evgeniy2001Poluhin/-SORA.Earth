"""There is an alert for a retrain loop that stopped, not only for one that failed.

#188. `sora-retrain-failed` counts failures, and for a month there were none:
`models/` became root-owned on 17 July, drift never fired, so the scheduler
never attempted a retrain. No attempts, no failures, and no failures read as
health.

The two rules ask different questions and only one of them can notice a system
that has stopped trying. This pins that both exist, and that the new one reads
the gauge rather than a counter -- a counter cannot express absence.
"""
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALERTS = os.path.join(REPO_ROOT, "grafana", "provisioning", "alerting", "alerts.yml")


@pytest.fixture(scope="module")
def rules():
    yaml = pytest.importorskip("yaml")
    with open(ALERTS, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    return {r["uid"]: r for g in doc["groups"] for r in g["rules"]}


def test_both_questions_are_asked(rules):
    """"Did one fail" and "has one succeeded lately" are not the same alert,
    and the month of silence answered the first with yes-nothing-failed."""
    assert "sora-retrain-failed" in rules
    assert "sora-retrain-stale" in rules


def test_the_staleness_rule_reads_the_gauge(rules):
    """A counter of failures cannot express "nothing ran". The gauge can."""
    expr = rules["sora-retrain-stale"]["data"][0]["model"]["expr"]

    assert "sora_retrain_seconds_since_success" in expr
    assert "increase(" not in expr


def test_never_having_succeeded_also_fires(rules):
    """-1 is the value the metric carries when no successful run exists. A
    threshold alone would miss it, since -1 is below any positive bound."""
    expr = rules["sora-retrain-stale"]["data"][0]["model"]["expr"]

    assert "== -1" in expr


def test_the_metric_it_alerts_on_is_declared(rules):
    """An alert on a metric nothing publishes is a rule that cannot fire."""
    with open(os.path.join(REPO_ROOT, "app", "prom_metrics.py"),
              encoding="utf-8") as fh:
        assert "sora_retrain_seconds_since_success" in fh.read()


def test_the_summary_does_not_claim_a_fault(rules):
    """Retraining is triggered by drift, so a quiet fortnight can be
    legitimate. An alert that asserts breakage where there may be none is one
    people learn to dismiss."""
    text = str(rules["sora-retrain-stale"]["annotations"])

    assert "can be legitimate" in text
    assert "check whether a" in text
