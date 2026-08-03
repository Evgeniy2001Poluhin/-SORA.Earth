"""
Centralized ingester runner.
Calls .fetch_with_retry() on each registered ingester, collects Signals,
and persists them (if persistence layer exists).
"""
from __future__ import annotations
import asyncio
import logging
from sqlalchemy import text

from app.ingesters.classification import classify_run
from datetime import datetime, timezone

from app.ingesters.base import Signal
from app.ingesters.sber_veb_baseline import SberVebBaselineIngester
from app.ingesters.rosstat import RosstatIngester

log = logging.getLogger(__name__)

# OpenAQ and Open-Meteo are owned exclusively by their hourly scheduled jobs
# (scheduled_openaq_ingestion / scheduled_openmeteo_ingestion in
# app/services/environmental/scheduler_jobs.py) — do not add them here, or
# their signals get persisted twice (once by the hourly job, once by this
# 24h runner).
INGESTERS = [
    SberVebBaselineIngester,
    RosstatIngester,
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
        # Shaped like every other return from here, and explicitly a write
        # failure.
        #
        # It used to return {"saved": 0, "error": "persist_unavailable"} -- no
        # `received`, no `accepted` -- so both .get() calls in the caller yielded
        # 0 and a missing persistence layer was classified as a source that had
        # nothing to give. A broken database reported as an empty API is the
        # same silent success this whole change exists to remove, reintroduced
        # in a new place by the change itself.
        log.warning("[runner] persist module not available")
        return {
            "received": len(list(signals)),
            "inserted": 0, "updated": 0, "accepted": 0, "rejected": 0,
            "duplicates": 0,
            "write_failed": True,
            "errors": ["persist_unavailable"],
        }

    result = persist_environmental_observations(signals, source)

    # The same key set as the failure path above, write_failed included. A
    # result whose shape depends on which branch produced it is one every caller
    # has to guess at, and the guess that started this was `.get(..., 0)` on keys
    # that were simply absent.
    return {
        "received": result.received,
        "inserted": result.inserted,
        "updated": result.updated,
        "accepted": result.accepted,
        "rejected": result.rejected,
        "duplicates": result.duplicates,
        "write_failed": False,
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


def _audit_finish(rid, verdict):
    """Record the verdict, not a hand-picked word.

    The status used to be passed in by the caller, which is how every run that
    did not raise came to be 'ok' -- including sixty-two openaq runs that wrote
    nothing. It is now derived by classify_run() from what the run actually
    achieved, and this function has no opinion about it.
    """
    if rid is None:
        return
    try:
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text(
                "UPDATE ingester_runs SET finished_at=now(), status=:st, "
                "rows_written=:rw, error=:err, "
                "execution_status=:ex, primary_source_status=:ps, "
                "data_outcome=:do, records_received=:rr, records_accepted=:ra, "
                "records_rejected=:rj, failure_reason=:fr WHERE id=:id"),
                {"st": verdict.status,
                 "rw": verdict.records_accepted,
                 "err": verdict.failure_reason,
                 "ex": verdict.execution_status,
                 "ps": verdict.primary_source_status,
                 "do": verdict.data_outcome,
                 "rr": verdict.records_received,
                 "ra": verdict.records_accepted,
                 "rj": verdict.records_rejected,
                 "fr": verdict.failure_reason,
                 "id": rid})
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
                "status": "ok",  # replaced below by the classified verdict
                "signals": len(signals),
                "regions": len({s.region_code for s in signals}),
                "persist": persist_result,
            }
            verdict = classify_run(
                received=persist_result.get("received", 0),
                accepted=persist_result.get("accepted", 0),
                rejected=persist_result.get("rejected", 0),
                write_failed=persist_result.get("write_failed", False),
            )
            stats["ingesters"][ing.name].update(verdict.as_dict())
            if verdict.status == "success":
                log.info("[runner] %s: %d signals", ing.name, len(signals))
            else:
                # Loud, because this is the case that used to be indistinguishable
                # from working. A degraded run is not an error and must not be
                # logged as one, but it cannot pass in silence either.
                log.warning("[runner] %s: %s -- %s (received=%d accepted=%d)",
                            ing.name, verdict.status, verdict.failure_reason,
                            verdict.records_received, verdict.records_accepted)
            _audit_finish(rid, verdict)
        except Exception as e:
            verdict = classify_run(raised=e)
            stats["ingesters"][ing.name] = {"status": "error", "error": str(e),
                                            **verdict.as_dict()}
            log.exception("[runner] %s failed: %s", ing.name, e)
            _audit_finish(rid, verdict)

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
