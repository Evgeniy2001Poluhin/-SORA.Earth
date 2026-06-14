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
    total_signals: list[Signal] = []

    for cls in INGESTERS:
        ing = cls()
        rid = _audit_start(ing.name)
        try:
            signals = await ing.fetch_with_retry()
            stats["ingesters"][ing.name] = {
                "status": "ok",
                "signals": len(signals),
                "regions": len({s.region_code for s in signals}),
            }
            total_signals.extend(signals)
            log.info("[runner] %s: %d signals", ing.name, len(signals))
            _audit_finish(rid, "ok", rows_written=len(signals))
        except Exception as e:
            stats["ingesters"][ing.name] = {"status": "error", "error": str(e)}
            log.exception("[runner] %s failed: %s", ing.name, e)
            _audit_finish(rid, "error", error=e)

    saved = _persist_signals(total_signals)

    try:
        from app.services.esg_aggregator import recalc_all_regions
        agg = recalc_all_regions()
        stats["aggregation"] = agg
        log.info("[runner] aggregation: %s", agg)
    except Exception as e:
        log.exception("[runner] aggregation failed: %s", e)
        stats["aggregation"] = {"status": "error", "error": str(e)}
    stats["total_signals"] = len(total_signals)
    stats["persisted"] = saved
    stats["finished_at"] = datetime.now(timezone.utc).isoformat()

    log.info("[runner] done: %d signals total, %d persisted", len(total_signals), saved)
    return stats


def run_all_ingesters_sync() -> dict:
    """Sync wrapper for APScheduler BackgroundScheduler."""
    return asyncio.run(run_all_ingesters())
