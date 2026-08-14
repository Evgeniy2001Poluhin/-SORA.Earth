"""One process must not publish one metric name from two sources.

#94: `/metrics` served the `prometheus_client` registry and
`/api/v1/metrics/prometheus` assembled its own text from the in-process
`METRICS` dict. Both returned 200 and plausible Prometheus text, and they
disagreed. Measured in one process at one instant:

    /metrics                     sora_predictions_total{model="rf"} 7.0
    /api/v1/metrics/prometheus   sora_predictions_total 0

Four names collided, and none of the 22 metrics in app/prom_metrics.py appeared
on the second path at all -- so setting a forecast gauge or a drift counter did
nothing to it, while eight documents told the reader to curl exactly that path
and grep for those metrics.

These tests are about the property, not the endpoint: two Prometheus surfaces
are fine as long as they cannot contradict each other.
"""
import pytest

pytestmark = pytest.mark.usefixtures("client")


def _names(text):
    """Series names, ignoring labels, HELP/TYPE and blank lines."""
    return {
        line.split("{")[0].split(" ")[0]
        for line in text.splitlines()
        if line.strip() and not line.startswith("#")
    }


def _series(text):
    """name{labels} -> value, for exact comparison."""
    out = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        key, _, value = line.rpartition(" ")
        out[key.strip()] = value.strip()
    return out


def test_both_prometheus_paths_report_the_same_metric_names(client):
    """The condition that made #94 hard to see, asserted directly.

    Warmed up first. The instrumentator's `http_*` family springs into
    existence when the first instrumented request finishes, and `/metrics`
    itself is not instrumented -- so without a warm-up the two scrapes differ
    by twelve names for a reason that has nothing to do with #94. The first
    version of this test failed exactly that way.
    """
    for _ in range(2):
        client.get("/health")
        client.get("/api/v1/metrics/prometheus")

    registry = client.get("/metrics")
    api = client.get("/api/v1/metrics/prometheus")

    assert registry.status_code == 200 and api.status_code == 200

    only_registry = _names(registry.text) - _names(api.text)
    only_api = _names(api.text) - _names(registry.text)

    assert only_api == set(), (
        f"/api/v1/metrics/prometheus publishes names that /metrics does not: "
        f"{sorted(only_api)}. Two Prometheus endpoints in one process now "
        f"disagree about what exists."
    )
    assert only_registry == set(), (
        f"/metrics publishes names missing from /api/v1/metrics/prometheus: "
        f"{sorted(only_registry)}"
    )


def test_a_metric_moved_on_the_registry_moves_on_both(client):
    """The discriminating case.

    Equal names would also hold if both endpoints were empty, or if both were
    stale. This moves a real counter and requires both to follow -- which the
    old hand-assembled endpoint could not do for any of the 22 declared
    metrics.
    """
    from app import prom_metrics as pm

    before = _series(client.get("/api/v1/metrics/prometheus").text)
    key = 'sora_predictions_total{model="test_one_surface"}'
    start = float(before.get(key, 0))

    pm.sora_predictions_total.labels(model="test_one_surface").inc(3)

    registry = _series(client.get("/metrics").text)
    api = _series(client.get("/api/v1/metrics/prometheus").text)

    assert float(registry[key]) == start + 3, "the registry did not record it"
    assert api.get(key) is not None, (
        "/api/v1/metrics/prometheus does not carry a registry metric at all -- "
        "this is the #94 state"
    )
    assert float(api[key]) == float(registry[key]), (
        f"same name, two values, one instant: registry {registry[key]} vs "
        f"api {api[key]}"
    )


def test_the_declared_metrics_actually_appear(client):
    """Named ones, not a count.

    `len(names) == 22` would pass on 22 wrong names. These four are the ones
    the documentation tells people to grep for.
    """
    from app import prom_metrics as pm

    pm.sora_forecast_mae.labels(metric="esg", model="ensemble").set(1.5)
    pm.sora_drift_detected.inc()

    names = _names(client.get("/api/v1/metrics/prometheus").text)

    for expected in (
        "sora_forecast_mae_current",
        "sora_drift_detected_total",
        "sora_app_info",
        "sora_predictions_total",
    ):
        assert expected in names, (
            f"{expected} is declared in app/prom_metrics.py and does not reach "
            f"the endpoint the docs point at"
        )


def test_the_operational_dict_is_still_served_as_json(client):
    """What the old endpoint published has not been dropped, only relabelled.

    Prometheus never scraped this -- infra/prometheus.yml reads `/metrics` at
    the root, which serves the registry; verified against production, where
    that path returns the text exposition and not this dict. So the data here
    was only ever JSON in Prometheus clothing.

    The per-endpoint breakdown moved behind an admin token in #49 -- it held
    one key per concrete request path, and nine routes embed an identifier. It
    is asserted below rather than dropped from this list, because "not
    published anonymously" and "gone" are different outcomes and this test
    exists to tell them apart.
    """
    body = client.get("/api/v1/metrics").json()

    for field in ("requests_total", "uptime_seconds", "avg_response_time_ms"):
        assert field in body, f"{field} disappeared with the hand-built exporter"

    from app.auth import create_access_token

    token = create_access_token({"sub": "admin", "role": "admin"})
    admin_body = client.get(
        "/api/v1/metrics", headers={"Authorization": f"Bearer {token}"}).json()

    for field in ("requests_by_endpoint", "requests_by_status"):
        assert field in admin_body, f"{field} disappeared, rather than moving"


def test_the_scrape_config_still_points_at_the_registry_path():
    """The endpoint being fixed must not be read as licence to scrape it.

    Both paths are equivalent now, but only one is configured, and a change
    here should be a decision rather than a side effect.
    """
    import os

    import yaml

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(repo_root, "infra", "prometheus.yml")) as fh:
        config = yaml.safe_load(fh)

    paths = {job.get("metrics_path") for job in config["scrape_configs"]}
    assert paths == {"/metrics"}, paths
