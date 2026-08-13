"""
Centralized ingester runner.
Calls .fetch_with_retry() on each registered ingester, collects Signals,
and persists them (if persistence layer exists).
"""
from __future__ import annotations
import asyncio
import logging
from sqlalchemy import text

from app.ingesters.classification import (
    classify_run, freshness_status, required_action,
)
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

    # A persistence failure inside the upsert counts too, not only a missing
    # module.
    #
    # persist_environmental_observations catches its own exception, rolls back,
    # leaves accepted at 0 and appends an error prefixed "persist_error:" --
    # without raising. Detecting only the ImportError meant a database that
    # accepted nothing was still read as a source producing nothing usable:
    # REJECTED rather than a write failure, pointing the investigation at the
    # API while the database was the thing that broke.
    write_failed = any(
        str(e).startswith("persist_error:") for e in (result.errors or [])
    )

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
        "write_failed": write_failed,
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


# What a source's vintage is measured from, and when the question does not apply.
VINTAGE_MEASURED = "measured"
VINTAGE_NOT_APPLICABLE = "not_applicable"   # the source has no time dimension
VINTAGE_UNKNOWN = "unknown"                 # nothing stored, or the query failed


def source_vintage(source: str):
    """(vintage_seconds, basis) for the newest observation this source holds.

    The axis is chosen per `temporal_kind`, which is the whole point of #121:

        observed        event_time      -- a real observation instant
        period          period_end      -- the end of the period it covers
        not_applicable  no axis         -- the source has no time dimension
        legacy_*        excluded        -- its event_time is a stamped
                                           ingestion time, the defect #121 fixed

    It used to be `COALESCE(event_time, ingested_at)`. A `period` row has no
    event_time by construction, so rosstat's 2024 snapshot was aged from when it
    was last written -- and re-upserting it made it "fresh today". That is #121
    returning under a different name, in the field that decides what an operator
    is told to do.

    Three outcomes, not two. `not_applicable` is a category error rather than a
    missing measurement: asking whether a dated-nothing snapshot is stale has no
    answer, and reporting it as unmeasured would make every clean sber run
    demand attention forever. `unknown` is the honest "nobody looked", and
    `required_action` refuses to say "fine" on it.
    """
    try:
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            row = db.execute(text(
                "SELECT "
                "  EXTRACT(EPOCH FROM (now() - max(CASE "
                "     WHEN temporal_kind = 'observed' THEN event_time "
                "     WHEN temporal_kind = 'period'   THEN period_end "
                "  END))) AS age, "
                "  count(*) FILTER (WHERE temporal_kind = 'not_applicable') AS na, "
                # Legacy rows are excluded from the denominator as well as from
                # the axis. Counting them made `na == total` false for every
                # real source: sber_veb_baseline holds 85 not_applicable rows
                # beside 2040 legacy ones, so the state introduced for exactly
                # that source never fired and every clean run of it reported
                # `unknown` -> investigate. Measured on production.
                "  count(*) FILTER (WHERE temporal_kind <> 'legacy_ingestion_time') "
                "    AS datable_total "
                "FROM environmental_observations WHERE source = :s"),
                {"s": source}).one()
        finally:
            db.close()
    except Exception as e:
        log.warning("[runner] source_vintage failed for %s: %s", source, e)
        return None, VINTAGE_UNKNOWN

    age, not_applicable, datable_total = row
    if age is not None:
        return float(age), VINTAGE_MEASURED
    if datable_total and not_applicable == datable_total:
        # Every row that is not a legacy artefact is dated-nothing. Staleness
        # does not apply -- there is no answer, rather than an answer nobody
        # measured.
        return None, VINTAGE_NOT_APPLICABLE
    return None, VINTAGE_UNKNOWN


def _audit_finish(rid, verdict, action=None, vintage_seconds=None,
                  freshness=None, max_vintage_seconds=None):
    """Record the verdict, not a hand-picked word.

    The status used to be passed in by the caller, which is how every run that
    did not raise came to be 'ok' -- including sixty-two openaq runs that wrote
    nothing. It is now derived by classify_run() from what the run actually
    achieved, and this function has no opinion about it.

    `required_action` and every input it was derived from are stored alongside
    for the same
    reason the split verdict was: an operator reading `degraded` still cannot
    tell whether to wait, retry, or page anyone (#74). They are derived too --
    nothing here decides them.
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
                "records_rejected=:rj, failure_reason=:fr, "
                "reason_code=:rc, required_action=:act, "
                "source_vintage_seconds=:vintage, freshness_status=:fresh, "
                "max_vintage_seconds=:maxv "
                "WHERE id=:id"),
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
                 "rc": verdict.reason_code,
                 "act": action,
                 "vintage": vintage_seconds,
                 "fresh": freshness,
                 "maxv": max_vintage_seconds,
                 "id": rid})
            db.commit()
        finally:
            db.close()
    except Exception as e:
        log.warning("[runner] audit_finish failed: %s", e)


def _decide(ing, verdict):
    """(action, vintage_seconds, freshness, max_vintage_seconds) for one run.

    Every input to the verdict is returned, not just the verdict. A threshold
    can be changed after the fact, so a row holding only `escalate` and 590 days
    cannot be re-read a month later: nobody can tell whether it escalated
    because the data was old or because the tolerance moved.

    The tolerance is `max_vintage_hours`, which most sources do not declare.
    That is not an oversight to paper over -- see BaseIngester. It is emphatically
    **not** `default_ttl_hours`, which says when to poll: reusing it made every
    clean rosstat run escalate forever, 590 days of vintage against a 180-day
    poll of an annual statistic.
    """
    vintage, basis = source_vintage(ing.name)
    max_hours = getattr(ing, "max_vintage_hours", None)
    max_vintage = None if max_hours is None else float(max_hours) * 3600.0

    freshness = freshness_status(
        vintage,
        has_axis=(basis != VINTAGE_NOT_APPLICABLE),
        max_vintage_seconds=max_vintage,
    )
    return required_action(verdict, freshness=freshness), vintage, freshness, max_vintage


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
            action, vintage, freshness, max_vintage = _decide(ing, verdict)
            stats["ingesters"][ing.name].update(verdict.as_dict())
            stats["ingesters"][ing.name].update({
                "required_action": action,
                "source_vintage_seconds": vintage,
                "freshness_status": freshness,
                "max_vintage_seconds": max_vintage,
            })
            if verdict.status == "success":
                log.info("[runner] %s: %d signals", ing.name, len(signals))
            else:
                # Loud, because this is the case that used to be indistinguishable
                # from working. A degraded run is not an error and must not be
                # logged as one, but it cannot pass in silence either.
                log.warning(
                    "[runner] %s: %s [%s] action=%s freshness=%s -- %s "
                    "(received=%d accepted=%d vintage=%s)",
                    ing.name, verdict.status, verdict.reason_code, action,
                    freshness, verdict.failure_reason,
                    verdict.records_received, verdict.records_accepted,
                    "unmeasured" if vintage is None else int(vintage))
            _audit_finish(rid, verdict, action=action, vintage_seconds=vintage,
                          freshness=freshness, max_vintage_seconds=max_vintage)
        except Exception as e:
            verdict = classify_run(raised=e)
            action, vintage, freshness, max_vintage = _decide(ing, verdict)
            stats["ingesters"][ing.name] = {"status": "error", "error": str(e),
                                            "required_action": action,
                                            "source_vintage_seconds": vintage,
                                            "freshness_status": freshness,
                                            "max_vintage_seconds": max_vintage,
                                            **verdict.as_dict()}
            log.exception("[runner] %s failed: %s", ing.name, e)
            _audit_finish(rid, verdict, action=action, vintage_seconds=vintage,
                          freshness=freshness, max_vintage_seconds=max_vintage)

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
