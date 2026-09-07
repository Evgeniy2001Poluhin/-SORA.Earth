"""Serving the scheduler process's own metrics (#267).

A module of its own, and not a few lines inside `run_scheduler.py`, for the
reason that has come up twice already in this repository: importing
`run_scheduler` pulls in `app.scheduler`, which pulls in APScheduler and the
whole application. A test of a socket and a registry should not need any of
that, and one that does ends up not being written.

Eleven `sora_*` metrics are written **only** in the scheduler container --
`sora_retrain_total`, `sora_full_pipeline_total`, the four forecast gauges and
the five environmental ones. That process served no HTTP and
`infra/prometheus.yml` named one target, `backend:8000`, so every one of them
was set into the memory of a process nobody asked and lost on the next restart.
`sora_retrain_total{status}` is what CLAUDE.md calls a key metric.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

#: Inside the compose network only. `docker-compose.prod.yml` gives the
#: scheduler no `ports:`, so nothing here is reachable from the host, and
#: `tests/test_scheduler_metrics_are_scraped.py` asserts that it stays that way.
DEFAULT_PORT = 9000


def metrics_port() -> int:
    return int(os.getenv("SORA_SCHEDULER_METRICS_PORT", str(DEFAULT_PORT)))


def start_metrics_server() -> bool:
    """Publish this process's metrics. Returns whether the server is listening.

    A single process, so no multiprocess directory: `prometheus_client` writes
    to memory-mapped files only when `PROMETHEUS_MULTIPROC_DIR` is set, and it
    must not be set here. `entrypoint.sh` sets it after the branch this
    container leaves through, and sharing the backend's directory would mix two
    applications' lifetimes into one set of files (#262).

    A failure to bind is logged and not raised. The scheduler's job is to run
    the jobs; losing the metrics endpoint must not stop them.
    """
    if os.getenv("PROMETHEUS_MULTIPROC_DIR"):
        # Loud, because what would then be published is whatever those files
        # hold rather than this process's counters -- a metrics endpoint that
        # answers, with the wrong numbers.
        logger.error(
            "PROMETHEUS_MULTIPROC_DIR is set in the scheduler; it must not be. "
            "Metrics are not being served."
        )
        return False

    port = metrics_port()
    try:
        from prometheus_client import start_http_server

        start_http_server(port)
    except Exception as exc:
        logger.error("Could not start the metrics server on :%d: %s", port, type(exc).__name__)
        return False

    logger.info("Scheduler metrics on :%d/metrics", port)
    return True
