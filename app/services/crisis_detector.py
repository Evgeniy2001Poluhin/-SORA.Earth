"""Environmental crisis detection based on threshold violations."""
from datetime import datetime, timedelta
from typing import Optional
import json
import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# WHO + EU thresholds for 6-hour observation window
CRISIS_THRESHOLDS = {
    "pm25_ugm3":  {"limit": 75.0,  "crisis_type": "air_quality",   "severity_base": 60},
    "no2_ugm3":   {"limit": 200.0, "crisis_type": "air_quality",   "severity_base": 50},
    "o3_ugm3":    {"limit": 160.0, "crisis_type": "air_quality",   "severity_base": 45},
    "so2_ugm3":   {"limit": 125.0, "crisis_type": "air_quality",   "severity_base": 40},
}

def detect_crises(db: Session) -> int:
    """
    Ccrises logged.
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
