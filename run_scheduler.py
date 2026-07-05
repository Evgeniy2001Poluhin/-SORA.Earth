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

logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
logger = logging.getLogger("run_scheduler")


def publish_scheduler_status():
    """Publish scheduler status to Redis for API consumption."""
    try:
        from app.redis_cache import redis_client, REDIS_AVAILABLE
        import json

        if not REDIS_AVAILABLE:
            return

        jobs = []
        for j in scheduler.get_jobs():
            jobs.append({
                "id": j.id,
                "name": j.name,
                "trigger": str(j.trigger),
                "next_run": str(j.next_run_time) if j.next_run_time else None,
            })

        status = {
            "running": scheduler.running,
            "jobs": jobs,
            "jobs_count": len(jobs),
            "last_updated": datetime.utcnow().isoformat() + "Z",
        }

        redis_client.setex(
            "sora:scheduler:status",
            120,  # TTL 2 minutes
            json.dumps(status)
        )
    except Exception as e:
        logger.warning("Failed to publish scheduler status to Redis: %s", e)


if __name__ == "__main__":
    logger.info("Starting dedicated scheduler process...")
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
