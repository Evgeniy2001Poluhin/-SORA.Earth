"""Environmental crisis detection based on threshold violations."""
from datetime import datetime, timedelta
from typing import Optional
import json
import logging
import os

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# WHO + EU thresholds for 6-hour observation window
CRISIS_THRESHOLDS = {
    "pm25_ugm3":  {"limit": 75.0,  "crisis_type": "air_quality",   "severity_base": 60},
    "no2_ugm3":   {"limit": 200.0, "crisis_type": "air_quality",   "severity_base": 50},
    "o3_ugm3":    {"limit": 160.0, "crisis_type": "air_quality",   "severity_base": 45},
    "so2_ugm3":   {"limit": 125.0, "crisis_type": "air_quality",   "severity_base": 40},
}


def crisis_detection_scheduling_refusal() -> Optional[str]:
    """Why crisis detection must not be scheduled, or None if it may be.

    This detector queries the `region_signals` table for OpenAQ metric names
    (pm25_ugm3, no2_ugm3, o3_ugm3, so2_ugm3). Nothing has written to that table
    since the ingester runner moved to `environmental_observations` on 2026-07-30
    (see app/services/esg_aggregator.py module docstring, #116). OpenAQ itself was
    stood down because its stations stopped reporting in September 2017 (#57).

    The job structurally finds zero violations on every run and only logs. Off by
    default: a detector that cannot detect should not become a scheduled job as a
    side effect of a deployment.

    Returns:
        None if crisis detection should be scheduled, or a reason string if not.
    """
    enabled = os.getenv("SORA_CRISIS_DETECTION_ENABLED", "").strip().lower()
    if enabled in {"1", "true", "yes", "on", "enabled"}:
        return None
    return (
        "reads region_signals table, which nothing has written since 2026-07-30 "
        "when the ingester runner moved to environmental_observations; "
        "metric names are OpenAQ's (pm25_ugm3, no2_ugm3, o3_ugm3, so2_ugm3) "
        "and OpenAQ stood down in #57"
    )


def detect_crises(db: Session) -> int:
    """Scan for environmental threshold violations in the last 6 hours.

    Queries `region_signals` for air quality metrics exceeding WHO/EU thresholds.
    Logs each violation with region, metric, value, threshold and severity.

    Returns:
        Number of threshold violations found.
    """
    from app.database import RegionSignal

    cutoff = datetime.utcnow() - timedelta(hours=6)
    new_crises = 0

    for metric, cfg in CRISIS_THRESHOLDS.items():
        try:
            violations = (
                db.query(RegionSignal)
                .filter(
                    RegionSignal.metric == metric,
                    RegionSignal.observed_at >= cutoff,
                    RegionSignal.value > cfg["limit"],
                )
                .all()
            )
            for v in violations:
                severity = min(100.0, (v.value / cfg["limit"]) * cfg["severity_base"])
                logger.warning(
                    "Crisis detected: region=%s metric=%s value=%.2f threshold=%.2f severity=%.1f",
                    v.region_code, metric, v.value, cfg["limit"], severity,
                )
                new_crises += 1
        except Exception:
            logger.exception("Error checking metric=%s", metric)

    return new_crises
