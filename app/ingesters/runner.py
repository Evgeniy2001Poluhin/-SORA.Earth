"""
Centralized ingester runner.
Calls .fetch_with_retry() on each registered ingester, collects Signals,
and persists them (if persistence layer exists).
"""
from __future__ import annotations
import asyncio
import logging
from sqlalchemy import text

from app.ingesters.classification import classify_run, required_action
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
    """(age_seconds, basis) for the newest observation this source holds.

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


def _audit_finish(rid, verdict, action=None, age_seconds=None):
    """Record the verdict, not a hand-picked word.

    The status used to be passed in by the caller, which is how every run that
    did not raise came to be 'ok' -- including sixty-two openaq runs that wrote
    nothing. It is now derived by classify_run() from what the run actually
    achieved, and this function has no opinion about it.

    `required_action` and `source_age_seconds` are stored alongside for the same
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
                "reason_code=:rc, required_action=:act, source_age_seconds=:age "
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
                 "age": age_seconds,
                 "id": rid})
            db.commit()
        finally:
            db.close()
    except Exception as e:
        log.warning("[runner] audit_finish failed: %s", e)


def _decide(ing, verdict):
    """(required_action, source_age_seconds) for one finished run.

    The tolerance is the ingester's own `default_ttl_hours` -- 1 hour for
    openmeteo, 180 days for rosstat. A source is not stale because some global
    threshold says so; it is stale against the cadence it declares for itself.

    Freshness is measured for **every** outcome, including a clean one. An
    earlier version skipped it when the run succeeded, to save a round trip on
    the common path. That made a successful write of a 2017 snapshot report
    `none` -- a working pipeline over a dead source, described as nothing to do,
    which is the exact conflation #74 exists to undo.
    """
    age, basis = source_vintage(ing.name)
    tolerance = getattr(ing, "default_ttl_hours", None)
    tolerance = None if tolerance is None else float(tolerance) * 3600.0
    action = required_action(
        verdict,
        age_seconds=age,
        tolerance_seconds=tolerance,
        freshness_applies=(basis != VINTAGE_NOT_APPLICABLE),
    )
    return action, age


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
            action, age = _decide(ing, verdict)
            stats["ingesters"][ing.name].update(verdict.as_dict())
            stats["ingesters"][ing.name]["required_action"] = action
            stats["ingesters"][ing.name]["source_age_seconds"] = age
            if verdict.status == "success":
                log.info("[runner] %s: %d signals", ing.name, len(signals))
            else:
                # Loud, because this is the case that used to be indistinguishable
                # from working. A degraded run is not an error and must not be
                # logged as one, but it cannot pass in silence either.
                log.warning(
                    "[runner] %s: %s [%s] action=%s -- %s "
                    "(received=%d accepted=%d age=%s)",
                    ing.name, verdict.status, verdict.reason_code, action,
                    verdict.failure_reason, verdict.records_received,
                    verdict.records_accepted,
                    "unknown" if age is None else int(age))
            _audit_finish(rid, verdict, action=action, age_seconds=age)
        except Exception as e:
            verdict = classify_run(raised=e)
            action, age = _decide(ing, verdict)
            stats["ingesters"][ing.name] = {"status": "error", "error": str(e),
                                            "required_action": action,
                                            "source_age_seconds": age,
                                            **verdict.as_dict()}
            log.exception("[runner] %s failed: %s", ing.name, e)
            _audit_finish(rid, verdict, action=action, age_seconds=age)

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
