"""Every panel Grafana loads queries a metric the platform exports.

The provisioned dashboard "SORA Forecasting Models" had seven panels, and not
one of them could show anything. They queried

    forecast_lstm_active   forecast_sample_count   forecast_days_until_lstm
    forecast_lstm_weight   forecast_prophet_weight forecast_requests_total
    forecast_model_mae

and the platform exports none of those names. It exports
`sora_forecast_lstm_active`, `sora_forecast_samples_total`,
`sora_forecast_days_remaining` and `sora_forecast_mae_current`; the ensemble
weights and a forecast request counter do not exist under any name. A panel over
a name with no series reads "No data" forever, which looks like a quiet system
rather than an unwired one.

`tests/test_every_metric_has_a_writer.py` guards the alert rules, and it looks
only for names beginning `sora_` -- so a name without the prefix is invisible to
it by construction, which is exactly the shape of every name above. This file
checks every metric name in every expression, in the dashboards Grafana
actually loads.

## What counts as existing

Whatever the application's registry exposes after one request -- the declared
`sora_*` metrics, the HTTP instrumentation (`http_requests_total`,
`http_request_duration_seconds_bucket`, ...) and the default process
collectors -- plus the declared `sora_*` names read from `app/` by
`_defined_metrics()`, for any declared in a module this process did not import,
plus `up`, which Prometheus synthesises per target. Measured rather than listed,
so an allowance cannot outlive the thing it allowed.

## Which dashboards

`docker-compose.prod.yml` mounts `./grafana/provisioning/dashboards`, and
`dashboards.yml` points the provider there; `grafana/dashboards/` is mounted
nowhere. The first test pins that premise, because checking files Grafana never
reads would prove nothing about the ones it does.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PROVISIONED = ROOT / "grafana" / "provisioning" / "dashboards"

#: Words that can stand where a metric name stands without being one.
_KEYWORDS = {"and", "or", "unless", "bool", "offset", "inf", "nan"}


def metric_names(expr: str) -> set[str]:
    """The metric names a PromQL expression reads.

    Label matchers, ranges, grouping clauses, strings and Grafana variables are
    removed first; a token followed by `(` is a function or aggregation.
    """
    e = re.sub(r'"(?:[^"\\]|\\.)*"', '""', expr)
    e = re.sub(r"'(?:[^'\\]|\\.)*'", "''", e)
    e = re.sub(r"\{[^}]*\}", "", e)
    e = re.sub(r"\[[^\]]*\]", "", e)
    e = re.sub(r"\b(by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)", "", e)
    e = re.sub(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?", "", e)
    names = set()
    for match in re.finditer(r"[A-Za-z_:][A-Za-z0-9_:]*", e):
        token = match.group(0)
        if e[match.end():].lstrip().startswith("("):
            continue
        if token.lower() in _KEYWORDS:
            continue
        names.add(token)
    return names


def _expressions(path: Path) -> list[tuple[str, str]]:
    """`(panel title, expr)` for every query in one dashboard."""
    found = []

    def walk(node, title):
        if isinstance(node, dict):
            title = node.get("title", title)
            if isinstance(node.get("expr"), str) and node["expr"].strip():
                found.append((title, node["expr"]))
            for value in node.values():
                walk(value, title)
        elif isinstance(node, list):
            for value in node:
                walk(value, title)

    walk(json.loads(path.read_text()), "?")
    return found


@pytest.fixture(scope="module")
def exported() -> set[str]:
    from fastapi.testclient import TestClient
    from prometheus_client import REGISTRY

    import app.main as main
    from tests.test_every_metric_has_a_writer import _defined_metrics

    # One request, so the HTTP instrumentation has registered its families.
    TestClient(main.app).get("/health")
    names = {"up"}
    for family in REGISTRY.collect():
        names.add(family.name)
        names.update(sample.name for sample in family.samples)
    for declared in _defined_metrics().values():
        names.update({declared, f"{declared}_total", f"{declared}_bucket",
                      f"{declared}_count", f"{declared}_sum"})
    return names


def test_grafana_loads_the_directory_this_file_checks():
    compose = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text())
    mounts = compose["services"]["grafana"]["volumes"]
    assert "./grafana/provisioning/dashboards:/etc/grafana/provisioning/dashboards" in mounts, (
        f"production Grafana no longer mounts the directory this file reads: {mounts}"
    )
    provider = yaml.safe_load((PROVISIONED / "dashboards.yml").read_text())
    paths = {p["options"]["path"] for p in provider["providers"]}
    assert paths == {"/etc/grafana/provisioning/dashboards"}, paths
    assert sorted(PROVISIONED.glob("*.json")), "no provisioned dashboards found"


def test_the_extractor_reads_names_and_nothing_else():
    """Controls for `metric_names`, on the shapes the dashboards use."""
    assert metric_names("forecast_lstm_active") == {"forecast_lstm_active"}
    assert metric_names("rate(forecast_requests_total[5m])") == {"forecast_requests_total"}
    assert metric_names(
        'histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket'
        '{job="sora-app", handler=~"/api/.*"}[5m])) by (le))'
    ) == {"http_request_duration_seconds_bucket"}
    assert metric_names('sum by (job) (increase(sora_retrain_total{status="ok"}[1h]))') == {
        "sora_retrain_total"}
    assert metric_names('sora_forecast_mae_current{model="ensemble"} > bool 0') == {
        "sora_forecast_mae_current"}


def test_the_exported_set_is_measured_not_assumed(exported):
    """The known set must hold the library metrics and must not hold anything."""
    assert "http_requests_total" in exported, (
        "the HTTP instrumentation's counter is missing from the registry, so "
        "the measurement below would be comparing against the wrong set"
    )
    assert "sora_forecast_lstm_active" in exported
    assert "forecast_lstm_active" not in exported


@pytest.mark.parametrize(
    "dashboard", sorted(p.name for p in PROVISIONED.glob("*.json")))
def test_every_panel_queries_a_metric_that_exists(dashboard, exported):
    missing = []
    for title, expr in _expressions(PROVISIONED / dashboard):
        for name in sorted(metric_names(expr) - exported):
            missing.append(f"{title!r}: {name}   ({expr})")
    assert not missing, (
        f"{dashboard}: these panels query names the platform does not export, "
        f"so they can only ever show 'No data':\n  " + "\n  ".join(missing)
    )
