"""Every provisioned Grafana alert has the shape that yields one number per series,
explicit no-data and error states, and a datasource that is actually declared.

This reads the provisioning files. It cannot show that Grafana evaluates them;
tests/grafana_stand does that against a real Grafana, outside CI.
"""
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAFANA = os.path.join(REPO_ROOT, "grafana", "provisioning")
ALERTS = os.path.join(GRAFANA, "alerting", "alerts.yml")
DATASOURCE_DIR = os.path.join(GRAFANA, "datasources")

#: The uid production's datasource already has. Changing it is not an in-place
#: update on Grafana 13.2.3 (measured on the stand), so it is pinned here.
DATASOURCE_UID = "PBFA97CFB590B2093"
EXPR_DATASOURCE = "__expr__"
ALLOWED_NODATA = {"OK", "Alerting"}


@pytest.fixture(scope="module")
def doc():
    yaml = pytest.importorskip("yaml")
    with open(ALERTS, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def rules(doc):
    return {r["uid"]: r for g in doc["groups"] for r in g["rules"]}


def _evaluate(evaluator, value):
    """Grafana's threshold comparison, for the two types these rules use."""
    ops = {"gt": lambda v, p: v > p, "lt": lambda v, p: v < p}
    return ops[evaluator["type"]](value, evaluator["params"][0])


def test_the_datasource_is_declared_once_and_is_the_default(doc):
    yaml = pytest.importorskip("yaml")
    files = sorted(os.listdir(DATASOURCE_DIR))

    assert files == ["datasource.yml"], files
    with open(os.path.join(DATASOURCE_DIR, "datasource.yml"), encoding="utf-8") as fh:
        declared = yaml.safe_load(fh)["datasources"]
    assert len(declared) == 1
    assert declared[0]["name"] == "Prometheus"
    assert declared[0]["uid"] == DATASOURCE_UID
    assert declared[0]["orgId"] == 1
    assert declared[0]["isDefault"] is True


def test_every_rule_queries_the_declared_datasource(rules):
    for uid, rule in rules.items():
        queries = [d for d in rule["data"] if d["datasourceUid"] != EXPR_DATASOURCE]
        assert len(queries) == 1, uid
        assert queries[0]["datasourceUid"] == DATASOURCE_UID, uid


def test_every_rule_is_an_instant_query_reduced_then_thresholded(rules):
    """A is an instant query, B reduces it to one value, C thresholds that value,
    and C is the condition. Without this a range query reaches the threshold."""
    for uid, rule in rules.items():
        nodes = {d["refId"]: d for d in rule["data"]}
        assert rule["condition"] == "C", uid
        assert nodes["A"]["model"]["instant"] is True, uid
        assert nodes["B"]["datasourceUid"] == EXPR_DATASOURCE, uid
        assert nodes["B"]["model"]["type"] == "reduce", uid
        assert nodes["B"]["model"]["expression"] == "A", uid
        assert nodes["C"]["datasourceUid"] == EXPR_DATASOURCE, uid
        assert nodes["C"]["model"]["type"] == "threshold", uid
        assert nodes["C"]["model"]["expression"] == "B", uid


def test_every_rule_states_its_no_data_and_error_behaviour(rules):
    for uid, rule in rules.items():
        assert rule.get("noDataState") in ALLOWED_NODATA, uid
        assert rule.get("execErrState") == "Alerting", uid


def test_the_app_down_rule_fires_on_a_down_target(rules):
    """The old `up == 0` returned the value 0 and Grafana read 0 as Normal, so a
    down target never alerted. The threshold is `lt 1`: a 0 must cross it."""
    rule = rules["sora-app-down"]
    nodes = {d["refId"]: d for d in rule["data"]}

    assert nodes["A"]["model"]["expr"] == 'up{job="sora-app"}'
    evaluator = nodes["C"]["model"]["conditions"][0]["evaluator"]
    assert evaluator == {"type": "lt", "params": [1]}
    assert _evaluate(evaluator, 0) is True, "a down target must alert"
    assert _evaluate(evaluator, 1) is False, "a healthy target must stay Normal"


def test_the_app_down_rule_alerts_when_the_target_is_absent(rules):
    """If the job is removed from the scrape config the series disappears. That is
    not healthy, so no data alerts."""
    assert rules["sora-app-down"]["noDataState"] == "Alerting"


def test_the_drift_and_retrain_rules_treat_a_quiet_counter_as_healthy(rules):
    """A counter that never moved is absent until its first increment. Absence is
    the healthy state for these, not an alert."""
    for uid in ("sora-drift-detected", "sora-retrain-failed"):
        assert rules[uid]["noDataState"] == "OK", uid


def _drift_expr(rules):
    return next(d["model"]["expr"] for d in rules["sora-drift-unmeasured-with-traffic"]["data"]
                if "expr" in d["model"])


def test_the_drift_unmeasured_rule_treats_absence_as_unmeasured(rules):
    """A missing sample on the measured side must read as unmeasured. Without
    `vector(1) unless` it would be NoData, which Grafana maps to OK: an absent
    drift series would then look healthy (review of this change, item 2)."""
    expr = _drift_expr(rules)
    assert expr.startswith("(vector(1) unless on() ("), expr
    assert ") and on() (sum(increase(" in expr, expr


def test_the_drift_unmeasured_rule_reads_the_latest_check_across_jobs(rules):
    """An old backend `measured=1` must not outvote a newer scheduler check. The
    expression selects the status of the newest check (by last-run timestamp),
    across the two jobs that run the loop, and bounds its age (review of this change, P2)."""
    expr = _drift_expr(rules)
    assert 'job=~"sora-app|sora-scheduler"' in expr, expr
    assert "and on(job)" in expr and "group_left()" in expr, expr
    assert "max(sora_drift_check_last_run_timestamp_seconds" in expr, expr
    assert "== 1)" in expr, "measured must be exactly 1, not merely non-zero"
    assert "< 93600" in expr, "26h freshness bound"
    assert "!= bool 1" not in expr, "the single-max form that let an old measurement win"


def test_the_drift_unmeasured_rule_pins_the_traffic_counter_to_the_backend(rules):
    expr = _drift_expr(rules)
    assert 'http_requests_total{job="sora-app",handler=~' in expr, expr


def test_the_drift_unmeasured_rule_is_silent_without_traffic(rules):
    """No traffic means no data, and no data is OK: nothing to say about drift."""
    assert rules["sora-drift-unmeasured-with-traffic"]["noDataState"] == "OK"


def test_the_stale_summary_states_the_real_delay(doc):
    """The hold is 45 minutes after the age passes 15, so from the last successful
    report the alert takes about an hour. A summary of "45 minutes" misstates it."""
    rule = next(r for g in doc["groups"] for r in g["rules"]
                if r["uid"] == "sora-observation-coverage-stale")
    assert "45 minutes" not in rule["annotations"]["summary"]
    assert "hour" in rule["annotations"]["summary"]
