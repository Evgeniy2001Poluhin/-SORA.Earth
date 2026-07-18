"""
Centralized ingester runner.
Calls .fetch_with_retry() on each registered ingester, collects Signals,
and persists them (if persistence layer exists).
"""
from __future__ import annotations
import asyncio
import logging
from sqlalchemy import text
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


def _persist_signals(signals: list[Signal], source: str) -> dict:
    """Persist signals to DB with upsert logic.

    Args:
        signals: List of Signal objects
        source: Data source name for conflict resolution

    Returns:
        Dict with persist statistics
    """
    try:
        from app.ingesters.persist import persist_environmental_observations
    except ImportError:
        log.warning("[runner] persist module not available")
        return {"saved": 0, "error": "persist_unavailable"}

    result = persist_environmental_observations(signals, source)

    return {
        "received": result.received,
        "inserted": result.inserted,
        "updated": result.updated,
        "accepted": result.accepted,
        "rejected": result.rejected,
        "duplicates": result.duplicates,
        "errors": result.errors[:5] if result.errors else [],
    }



def _audit_start(source: str):
    try:
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            rid = db.execute(text(
                "INSERT INTO ingester_runs (source, status) VALUES (:s, 'running') RETURNING id"),
                {"s": source}).scalar()
            db.commit()
            return rid
        finally:
            db.close()
    except Exception as e:
        log.warning("[runner] audit_start failed for %s: %s", source, e)
        return None


def _audit_finish(rid, status, rows_written=None, error=None):
    if rid is None:
        return
    try:
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text(
                "UPDATE ingester_runs SET finished_at=now(), status=:st, "
                "rows_written=:rw, error=:err WHERE id=:id"),
                {"st": status, "rw": rows_written,
                 "err": (str(error)[:2000] if error else None), "id": rid})
            db.commit()
        finally:
            db.close()
    except Exception as e:
        log.warning("[runner] audit_finish failed: %s", e)


async def run_all_ingesters() -> dict:
    """Run all registered ingesters, return stats."""
    stats = {"started_at": datetime.now(timezone.utc).isoformat(), "ingesters": {}}

    for cls in INGESTERS:
        ing = cls()
        rid = _audit_start(ing.name)
        try:
            signals = await ing.fetch_with_retry()

            # Persist signals immediately per source
            persist_result = _persist_signals(signals, ing.name)

            stats["ingesters"][ing.name] = {
                "status": "ok",
                "signals": len(signals),
                "regions": len({s.region_code for s in signals}),
                "persist": persist_result,
            }
            log.info("[runner] %s: %d signals", ing.name, len(signals))
            _audit_finish(rid, "ok", rows_written=persist_result.get("accepted", 0))
        except Exception as e:
            stats["ingesters"][ing.name] = {"status": "error", "error": str(e)}
            log.exception("[runner] %s failed: %s", ing.name, e)
            _audit_finish(rid, "error", error=e)

    try:
        from app.services.esg_aggregator import recalc_all_regions
        agg = recalc_all_regions()
        stats["aggregation"] = agg
        log.info("[runner] aggregation: %s", agg)
    except Exception as e:
        log.exception("[runner] aggregation failed: %s", e)
        stats["aggregation"] = {"status": "error", "error": str(e)}

    # Aggregate persist stats
    total_received = sum(
        ing_stats.get("persist", {}).get("received", 0)
        for ing_stats in stats["ingesters"].values()
        if isinstance(ing_stats, dict)
    )
    total_accepted = sum(
        ing_stats.get("persist", {}).get("accepted", 0)
        for ing_stats in stats["ingesters"].values()
        if isinstance(ing_stats, dict)
    )

    stats["total_signals"] = total_received
    stats["total_persisted"] = total_accepted
    stats["finished_at"] = datetime.now(timezone.utc).isoformat()

    log.info("[runner] done: %d signals total, %d persisted", total_received, total_accepted)
    return stats


def run_all_ingesters_sync() -> dict:
    """Sync wrapper for APScheduler BackgroundScheduler."""
    return asyncio.run(run_all_ingesters())
