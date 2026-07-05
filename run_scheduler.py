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
    """Publish scheduler status to Redis for API consumption (hardcoded jobs)."""
    try:
        from app.redis_cache import redis_client, REDIS_AVAILABLE
        import json

        if not REDIS_AVAILABLE:
            return

        logger.info("Publishing scheduler status to Redis...")

        status = {
            "running": True,
            "jobs": [
                {"id": "auto_closed_loop_daily", "name": "Daily closed-loop: drift -> retrain -> validate at 03:00 UTC", "trigger": "cron[hour='3', minute='0']"},
                {"id": "auto_refresh_external_data", "name": "Refresh external ESG data every 12h", "trigger": "interval[12:00:00]"},
                {"id": "auto_full_pipeline_weekly", "name": "Weekly full pipeline at Sun 03:30 UTC", "trigger": "cron[day_of_week='sun', hour='3', minute='30']"},
                {"id": "auto_run_ingesters", "name": "Run all ingesters every 24h", "trigger": "interval[24:00:00]"},
                {"id": "health_ping", "name": "Health ping every 5min", "trigger": "interval[0:05:00]"},
            ],
            "jobs_count": 5,
            "last_updated": datetime.utcnow().isoformat() + "Z",
        }
        redis_client.set("sora:scheduler:status", json.dumps(status), ex=120)
    except Exception as e:
        import traceback
        logger.error("Failed to publish scheduler status to Redis: %s\n%s", e, traceback.format_exc())


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
