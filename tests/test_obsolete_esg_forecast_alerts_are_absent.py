"""The closed M2 experiment must not keep paging operators (#284)."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ALERTS = ROOT / "grafana" / "provisioning" / "alerting" / "alerts.yml"

OBSOLETE_ESG_FORECAST_ALERTS = {
    "sora-forecast-mae-high",
    "sora-forecast-mae-spike",
    "sora-forecast-rmse-high",
    "sora-forecast-rmse-spike",
    "sora-forecast-r2-negative",
}

EXPECTED_OPERATIONAL_ALERTS = {
    "sora-drift-detected",
    "sora-retrain-stale",
    "sora-retrain-failed",
    "sora-high-latency",
    "sora-app-down",
}


def test_only_the_current_operational_alerts_are_provisioned():
    document = yaml.safe_load(ALERTS.read_text(encoding="utf-8"))
    provisioned = {
        rule["uid"]
        for group in document["groups"]
        for rule in group["rules"]
    }

    assert provisioned.isdisjoint(OBSOLETE_ESG_FORECAST_ALERTS)
    assert provisioned == EXPECTED_OPERATIONAL_ALERTS
