"""Gunicorn configuration. Exists for one hook (#262).

`prometheus_client`'s multiprocess mode keeps one set of memory-mapped files
per process id. When a worker dies -- a crash, a `--max-requests` recycle, a
graceful reload -- its files stay on disk and keep contributing their last
values to every future scrape. Counters would be fine; gauges would report the
state of a process that no longer exists, indefinitely.

`mark_process_dead` is what removes the gauge files for a departed worker. It
has to be called from the **master**, because the worker is already gone, and
`child_exit` is the only hook that runs there with the worker's pid in hand.

Counter and histogram files are deliberately **not** removed: their totals
belong to the series regardless of which process produced them, and deleting
them would make a worker recycle look like a drop in a monotonically
increasing counter.
"""
import os


def child_exit(server, worker):
    """Drop the dead worker's gauge files so its last values stop being served."""
    if not os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        return
    try:
        from prometheus_client import multiprocess

        multiprocess.mark_process_dead(worker.pid)
    except Exception as exc:  # pragma: no cover - never block a worker exit
        server.log.warning("prometheus mark_process_dead failed: %s", type(exc).__name__)
