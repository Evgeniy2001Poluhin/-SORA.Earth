"""The `/metrics` endpoint, aggregated across gunicorn's workers (#262).

The backend runs `gunicorn -w 4`, and `prometheus_client` keeps every metric in
the memory of the process that touched it. Measured on production 2026-09-06:
five scrapes of `/metrics` landed on two different workers, one reporting two
`sora_telemetry_tasks_total` series and the other reporting none, for an event
that had definitely happened. Prometheus scrapes one address, so it saw an
arbitrary worker out of four.

The error is **not** "about four times low". Gunicorn does not hand requests
round-robin, so for any given counter the scrape can be anywhere between the
full value and zero, and it moves between scrapes as a different worker answers.

`PROMETHEUS_MULTIPROC_DIR` makes each process write to memory-mapped files that
a collector reads back together. `prometheus_fastapi_instrumentator` already
does the right thing when that variable is set -- this module exists for the
one thing it cannot do.

**Why not just call `expose()`.** `sora_retrain_seconds_since_success` was
deliberately wired with `Gauge.set_function` in #188, so it is computed by
whoever collects rather than pushed by a runner: a value only a runner updates
goes stale exactly when the thing it measures goes wrong, and stops moving,
which reads as "recent". `set_function` does not survive multiprocess mode --
it is accepted without error and then never collected, measured -- so the
computation happens here instead, immediately before the registry is read. The
property #188 wanted is kept: the scrape triggers the calculation.
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest, multiprocess

logger = logging.getLogger(__name__)

router = APIRouter()

#: Set by `entrypoint.sh` for the gunicorn master before it forks, and only
#: there. The scheduler container runs a single process and must not share the
#: directory: it is scraped by nothing today, and pointing it at these files
#: would mix a second application's lifetime into them.
MULTIPROC_ENV = "PROMETHEUS_MULTIPROC_DIR"


def multiprocess_enabled() -> bool:
    return bool(os.environ.get(MULTIPROC_ENV))


def _refresh_collection_time_gauges() -> None:
    """Compute the gauges that are meant to be evaluated at scrape time.

    Failures are logged and swallowed: a metrics endpoint that returns 500
    because one gauge could not be computed takes every other metric with it,
    and the scrape is how anyone would find out.
    """
    try:
        from app.api.infra import retrain_staleness_seconds
        from app.prom_metrics import sora_retrain_seconds_since_success

        sora_retrain_seconds_since_success.set(retrain_staleness_seconds())
    except Exception as exc:  # pragma: no cover - telemetry must not break the scrape
        logger.warning("retrain staleness gauge not refreshed: %s", type(exc).__name__)


def build_registry() -> CollectorRegistry:
    """The registry this scrape should read.

    In multiprocess mode a fresh collector is built per scrape, which is how
    `prometheus_client` intends it: the collector reads the files as they are
    now, and keeping one around would cache a view of processes that have since
    come and gone.
    """
    if not multiprocess_enabled():
        from prometheus_client import REGISTRY

        return REGISTRY
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
    return registry


@router.get("/metrics", include_in_schema=False, response_class=Response)
def metrics() -> Response:
    """Prometheus text exposition. No JSON model exists for it, and the
    declared `response_class` is what says so to the contract inventory rather
    than leaving it counted as one more route nobody has typed."""
    _refresh_collection_time_gauges()
    return Response(content=generate_latest(build_registry()),
                    media_type=CONTENT_TYPE_LATEST)
