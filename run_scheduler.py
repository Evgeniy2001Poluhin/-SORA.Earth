import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
    force=True,
)

import time
from app.scheduler import init_scheduler, scheduler

logger = logging.getLogger("run_scheduler")

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
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Scheduler process stopped by signal.")
