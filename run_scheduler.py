import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
    force=True,
)

import time
from datetime import datetime

from app.scheduler import init_scheduler, scheduler
from app.scheduler_metrics import start_metrics_server

logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
logger = logging.getLogger("run_scheduler")


def publish_scheduler_status():
    """Publish what this process actually has, to Redis, for the API to read.

    This published a **hardcoded list of five jobs** (#270). There are
    thirteen, and one of the five carried a trigger the code has never had:
    `interval[12:00:00]` for `auto_refresh_external_data`, which is
    `IntervalTrigger(hours=6)`. `app/scheduler.py:get_scheduler_status()`
    returned that literal to `GET /api/v1/scheduler/status` tagged
    `source: "scheduler_container"` -- which reads as "this came from the
    scheduler". The operator's dashboard rendered `jobs.length` and showed 5.

    Same shape as the schedule section of CLAUDE.md before #252, but in the
    product rather than a document.

    `next_run` is included per job. Without it
    `app/api/admin_snapshot.py` computed `next_run_at` from a key that was
    never present, so the snapshot's "next run" could not be anything but
    null -- a value indistinguishable from "nothing is scheduled".
    """
    try:
        from app.redis_cache import redis_client, REDIS_AVAILABLE
        import json

        if not REDIS_AVAILABLE:
            return

        jobs = [
            {
                "id": job.id,
                "name": job.name,
                "trigger": str(job.trigger),
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            }
            for job in scheduler.get_jobs()
        ]

        status = {
            "running": scheduler.running,
            "jobs": jobs,
            # Derived, not stated. As a separate literal it was free to
            # disagree with the list beside it, and did: five against thirteen.
            "jobs_count": len(jobs),
            "last_updated": datetime.utcnow().isoformat() + "Z",
        }
        redis_client.set("sora:scheduler:status", json.dumps(status), ex=120)
    except Exception as e:
        import traceback
        logger.error("Failed to publish scheduler status to Redis: %s\n%s", e, traceback.format_exc())


if __name__ == "__main__":
    logger.info("Starting dedicated scheduler process...")
    start_metrics_server()
    init_scheduler()
    logger.info(
        "Scheduler state: running=%s, jobs=%d",
        scheduler.running,
        len(scheduler.get_jobs()),
    )
    for j in scheduler.get_jobs():
        logger.info("  job=%s next_run=%s", j.id, j.next_run_time)

    logger.info("Scheduler loop started. Press Ctrl+C to stop.")
    try:
        while True:
            publish_scheduler_status()
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Scheduler process stopped by signal.")
