"""
Centralized ingester runner.
Calls .fetch_with_retry() on each registered ingester, collects Signals,
and persists them (if persistence layer exists).
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone

from app.ingesters.base import Signal
from app.ingesters.openaq import OpenAQIngester
from app.ingesters.sber_veb_baseline import SberVebBaselineIngester
from app.ingesters.rosstat import RosstatIngester

log = logging.getLogger(__name__)

INGESTERS = [
    SberVebBaselineIngester,
    RosstatIngester,
    OpenAQIngester,
]


def _persist_signals(signals: list[Signal]) -> int:
    """Persist signals to DB. Falls back to no-op if models not available."""
    try:
        from app.database import SessionLocal
        from app.database import RegionSignal  # может отсутствовать
    except ImportError:
        log.warning("[runner] RegionSignal model s not persisted")
        return 0

    db = SessionLocal()
    saved = 0
    try:
        for s in signals:
            row = RegionSignal(
                region_code=s.region_code,
                source=s.source,
                metric=s.metric,
                value=s.value,
                unit=s.unit,
                observed_at=s.observed_at or datetime.now(timezone.utc),
            )
            db.add(row)
            saved += 1
        db.commit()
    except Exception as e:
        db.rollback()
        log.exception("[runner] persist failed: %s", e)
    finally:
        db.close()
    return saved


async def run_all_ingesters() -> dict:
    """Run all registered ingesters, return stats."""
    stats = {"started_at": datetime.now(timezone.utc).isoformat(), "ingesters": {}}
    total_signals: list[Signal] = []

    for cls in INGESTERS:
        ing = cls()
        try:
            signals = await ing.fetch_with_retry()
            stats["ingesters"][ing.name] = {
                "status": "ok",
                "signals": len(signals),
                "regions": len({s.region_code for s in signals}),
            }
            total_signals.extend(signals)
            log.info("[runner] %s: %d signals", ing.name, len(signals))
        except Exception as e:
            stats["ingesters"][ing.name] = {"status": "error", "error": str(e)}
            log.exception("[runner] %s failed: %s", ing.name, e)

    saved = _persist_signals(total_signals)
    stats["total_signals"] = len(total_signals)
    stats["persisted"] = saved
    stats["finished_at"] = datetime.now(timezone.utc).isoformat()

    log.info("[runner] done: %d signals total, %d persisted", len(total_signals), saved)
    return stats


def run_all_ingesters_sync() -> dict:
    """Sync wrapper for APScheduler BackgroundScheduler."""
    return asyncio.run(run_all_ingesters())
