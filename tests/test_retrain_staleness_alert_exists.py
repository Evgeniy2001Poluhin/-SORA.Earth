"""The retrain alerts ask two different questions, and neither asks "was a retrain needed".

#188 added an alert on the age of the last successful retrain. Decision
2026-10-21 (GPT-RETRAIN-ALERT-DECISION) removed it: a retrain that was never
needed looks identical to one that stopped, and the page fired on the first.
What a retrain loop can honestly be paged for is narrower:

  sora-retrain-failed                a run that was started and failed (counter)
  sora-retrain-history-unreadable    the history could not be read at all (gauge)

and drift that has gone unmeasured while the service is used is paged by
`sora-drift-unmeasured-with-traffic` (see test_drift_check_status_is_published).
This file pins that the age is no longer alerted on, and that the two that
remain read what they claim to.
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


def _exprs(rule):
    return [d["model"].get("expr", "") for d in rule["data"] if "expr" in d["model"]]


def test_the_stale_rule_is_gone(rules):
    """The rule whose firing on a quiet history was the defect."""
    assert "sora-retrain-stale" not in rules
    assert not any("No Successful Retrain" == r["title"] for r in rules.values())


def test_no_rule_alerts_on_the_age_of_the_last_success(rules):
    """A retrain that was never needed has an old last success. Nothing may page
    on that age -- it is exported for reading, not for alerting."""
    for uid, rule in rules.items():
        for expr in _exprs(rule):
            assert "sora_retrain_seconds_since_success" not in expr, uid


def test_retrain_failed_still_asks_whether_a_run_failed(rules):
    """An actual failed run is a fault and stays as it was."""
    expr = _exprs(rules["sora-retrain-failed"])[0]
    assert expr == "increase(sora_model_rejected_total[10m])"


def test_the_unreadable_history_rule_reads_the_readability_gauge(rules):
    rule = rules["sora-retrain-history-unreadable"]
    assert rule["title"] == "Retrain History Unreadable"
    expr = _exprs(rule)[0]
    assert expr == 'sora_retrain_history_readable{job="sora-app"} == bool 0'
    assert "sora_retrain_seconds_since_success" not in expr


def test_the_unreadable_history_rule_holds_for_fifteen_minutes(rules):
    """A single failed read during a database restart should not page."""
    assert rules["sora-retrain-history-unreadable"]["for"] == "15m"


def test_the_unreadable_history_rule_treats_absence_as_ok(rules):
    """Absent readability means the backend is not scraped; App Unreachable pages
    for that, so this rule does not page it a second time."""
    assert rules["sora-retrain-history-unreadable"]["noDataState"] == "OK"


def test_the_summary_does_not_claim_a_retrain_fault(rules):
    """It is a fault in the read path. Saying a retrain is broken would repeat the
    conflation this change removes."""
    text = str(rules["sora-retrain-history-unreadable"]["annotations"])
    assert "not a statement about whether a retrain is needed" in text
