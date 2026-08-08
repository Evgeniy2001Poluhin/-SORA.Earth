"""Silver layer: aggregate environmental_observations -> region_esg_scores.

This read `region_signals` until #116. That table took its last row on
2026-07-30, when the ingester runner moved to `persist_environmental_observations`
(`app/ingesters/runner.py:160`), and nothing noticed: the aggregator kept
recomputing the same 85 scores from frozen data and returning `{"status": "ok"}`
after every run.

The formula is unchanged. Both tables carry the same six `source:indicator`
pairs for the same 85 regions, so this moves where the numbers come from, not
what is computed from them.
"""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select

from app.database import SessionLocal, EnvironmentalObservation, RegionESGScore

log = logging.getLogger(__name__)


# The (source, indicator) pairs _compute_one reads, declared rather than left
# implicit in the formula. A source that stops publishing then shows up as a
# key missing from a named set, instead of as a value that is quietly None.
REQUIRED_METRICS = (
    ("sber_veb_baseline", "esg_index_baseline"),
    ("rosstat", "unemployment_rate"),
    ("rosstat", "avg_income_rub"),
    ("rosstat", "life_expectancy"),
    ("rosstat", "budget_transparency"),
    ("rosstat", "digital_gov_index"),
)

# Air quality is deliberately absent, and this is the third time the platform
# has had to decide it explicitly (M1 withdrew `air_quality` and `carbon_price`
# from the forecasting regressors for the same reason).
#
# The formula used to read `openaq:pm25_ugm3`. openaq has never written a row
# -- 0 in every one of its 62 recorded runs (#117) -- so that term has been
# None for every score the platform has produced, and `env_score` has been the
# sber_veb baseline for all 85 regions.
#
# `openmeteo_air_quality:pm2_5` *is* live and current. It is not wired in here
# because it covers 21 regions, of which only RU-MOW and RU-SPE appear in this
# table: `env_score` would then mean a measured air quality for 2 regions and a
# baseline index for the other 83, under one column name. Which regions the
# platform commits to scoring is the declaration in #114, not a decision to
# make in passing here.

# The score this file produces is a **structural cross-section**, not a
# reading of anything that changes over time, and it says so in its own output
# rather than leaving that to be inferred.
#
# Both sources behind REQUIRED_METRICS are static literals in the source tree:
# `sber_veb_baseline` is a hardcoded dict of 85 constants with no network call,
# and `rosstat` makes no network call either, importing
# `data.rosstat_snapshot_2024` -- an offline 2024 snapshot refreshed roughly
# half-yearly, per its own docstring. Measured over the 9 days held in
# `environmental_observations`, every one of the six metrics has exactly **one
# distinct value per region**, against 99.9 for openmeteo temperature over the
# same window.
#
# So the score is real between regions and constant within one. It is an index,
# and #114 established that no forecasting target can be built from it.
SCORE_KIND = "structural"

# Declared here because neither source reports its own vintage, and the row
# timestamps cannot stand in for it: both re-emit their values every run
# stamped with `now`, so `event_time` is always today while the numbers are
# from 2024. Writing `event_time = now` for a literal is a provenance defect in
# the ingesters and is tracked separately; until it is fixed, this is the only
# place the true vintage is stated.
SOURCE_DATA_VINTAGE = {
    "rosstat": "2024 offline snapshot (data.rosstat_snapshot_2024)",
    "sber_veb_baseline": "unversioned literal (app/ingesters/sber_veb_baseline.py)",
}

# How long ingestion may be silent before the run is degraded.
#
# This measures `ingested_at`: **whether the pipeline is running**, which is a
# real and previously invisible failure -- the aggregator read a table dead for
# eight days and reported ok (#116). It is deliberately *not* called freshness
# of the data, and it is not computed from `event_time`, because for these
# sources a current `event_time` means only that a constant was re-stamped.
_MAX_INGEST_AGE_HOURS = float(os.getenv("SORA_AGGREGATOR_MAX_INGEST_AGE_HOURS", "48"))


def _clip(v, lo=0, hi=100):
    return max(lo, min(hi, v))


def _norm(v, lo, hi):
    if v is None:
        return None
    return _clip(100 * (v - lo) / (hi - lo))


def _latest_by_region(db):
    """Newest valid observation per (region, source, indicator).

    Returns (metrics_by_region, newest_ingested_at, rows_examined).

    `ingested_at` rather than `event_time`, deliberately: the caller uses it to
    ask whether ingestion is still running, and `event_time` cannot answer that
    for a source that re-stamps a literal with `now`.

    `row_number()` rather than DISTINCT ON, matching app/services/point_in_time.py:
    production is PostgreSQL but the test suite runs on SQLite, and a read path
    that changes shape between them is worse than a longer query.
    """
    ranked = (
        select(
            EnvironmentalObservation.region_id,
            EnvironmentalObservation.source,
            EnvironmentalObservation.indicator,
            EnvironmentalObservation.value,
            EnvironmentalObservation.ingested_at,
            func.row_number()
            .over(
                partition_by=(
                    EnvironmentalObservation.region_id,
                    EnvironmentalObservation.source,
                    EnvironmentalObservation.indicator,
                ),
                order_by=(
                    EnvironmentalObservation.event_time.desc(),
                    EnvironmentalObservation.ingested_at.desc(),
                    EnvironmentalObservation.id.desc(),
                ),
            )
            .label("rn"),
        )
        .where(
            EnvironmentalObservation.is_valid.is_(True),
            EnvironmentalObservation.value.isnot(None),
            # An or_ of and_ pairs rather than tuple_(...).in_(...): row-value
            # IN is a place PostgreSQL and SQLite differ, and the test suite
            # runs on SQLite while production is PostgreSQL.
            or_(
                *[
                    and_(
                        EnvironmentalObservation.source == src,
                        EnvironmentalObservation.indicator == ind,
                    )
                    for src, ind in REQUIRED_METRICS
                ]
            ),
        )
        .subquery()
    )

    result = defaultdict(dict)
    newest_ingest = None
    examined = 0

    for region_id, source, indicator, value, ingested_at, _rn in db.execute(
        select(ranked).where(ranked.c.rn == 1)
    ):
        examined += 1
        result[region_id][f"{source}:{indicator}"] = (value, source)
        if ingested_at is not None and (
            newest_ingest is None or ingested_at > newest_ingest
        ):
            newest_ingest = ingested_at

    return result, newest_ingest, examined


def _get(metrics, key):
    t = metrics.get(key)
    return t[0] if t else None


def _compute_one(metrics):
    base = _get(metrics, "sber_veb_baseline:esg_index_baseline")

    unemp = _get(metrics, "rosstat:unemployment_rate")
    inc = _get(metrics, "rosstat:avg_income_rub")
    life = _get(metrics, "rosstat:life_expectancy")
    s_parts = []
    if unemp is not None:
        s_parts.append(_clip(100 - unemp * 4))
    if inc is not None:
        s_parts.append(_norm(inc, 20000, 130000))
    if life is not None:
        s_parts.append(_norm(life, 65, 82))

    budget = _get(metrics, "rosstat:budget_transparency")
    digital = _get(metrics, "rosstat:digital_gov_index")
    g_parts = [p for p in (budget, digital) if p is not None]

    # No air-quality term reaches this: see the note beside REQUIRED_METRICS.
    env = base
    soc = (sum(s_parts) / len(s_parts)) if s_parts else base
    gov = (sum(g_parts) / len(g_parts)) if g_parts else base

    if env is None and soc is None and gov is None:
        return {}

    env_v = env if env is not None else base or 0
    soc_v = soc if soc is not None else base or 0
    gov_v = gov if gov is not None else base or 0

    total = env_v * 0.4 + soc_v * 0.35 + gov_v * 0.25

    sources = {src for _, src in metrics.values()}
    confidence = min(1.0, len(sources) / 3.0)

    return {
        "env_score": round(env, 2) if env is not None else None,
        "social_score": round(soc, 2) if soc is not None else None,
        "gov_score": round(gov, 2) if gov is not None else None,
        "total_score": round(total, 2),
        "confidence": round(confidence, 2),
        "sources_count": len(sources),
        "signals_used": len(metrics),
    }


def _age_hours(newest):
    if newest is None:
        return None
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - newest).total_seconds() / 3600.0


def recalc_all_regions(max_ingest_age_hours: float | None = None):
    """Recompute every region's structural ESG index from the declared metrics.

    The report separates two things that a single "freshness" number ran
    together, and conflating them is what let an eight-day staleness pass
    unnoticed (#116):

    `pipeline_freshness` -- is ingestion still running? Measured on
    `ingested_at`. A stalled pipeline is a real failure and degrades the run.

    `source_data_vintage` -- how old is the *information*? Declared per source,
    because neither source reports it and both re-stamp their literals with
    `now`. No timestamp in the database can answer this, which is exactly why
    it is stated rather than measured.

    A fresh pipeline carrying 2024 numbers is the normal state here, not an
    anomaly, and the output must not read as though the values were observed
    today.

    `regions_written` counts rows the database actually changed. **Zero is the
    expected result for a structural index** whose inputs are static: the
    recomputed values are identical, SQLAlchemy emits no UPDATE, and
    `onupdate=func.now()` never fires. That is a property of the sources, not
    a fault in this job, so it is never treated as degraded.
    """
    limit = (
        _MAX_INGEST_AGE_HOURS if max_ingest_age_hours is None
        else max_ingest_age_hours
    )
    db = SessionLocal()
    try:
        latest, newest_ingest, examined = _latest_by_region(db)

        computed = 0
        written = 0
        for region_code, metrics in latest.items():
            scores = _compute_one(metrics)
            if not scores:
                continue
            computed += 1
            row = db.query(RegionESGScore).filter_by(region_code=region_code).first()
            if row:
                if any(getattr(row, k) != v for k, v in scores.items()):
                    for k, v in scores.items():
                        setattr(row, k, v)
                    written += 1
            else:
                db.add(RegionESGScore(region_code=region_code, **scores))
                written += 1
        db.commit()

        ingest_age = _age_hours(newest_ingest)
        stalled = ingest_age is not None and ingest_age > limit

        reasons = []
        if examined == 0:
            reasons.append("no valid observations for any required metric")
        if stalled:
            reasons.append(
                f"ingestion silent for {ingest_age:.1f}h, limit {limit:.0f}h"
            )

        result = {
            "status": "degraded" if reasons else "success",
            "score_kind": SCORE_KIND,
            "regions_computed": computed,
            "regions_written": written,
            "observations_examined": examined,
            "pipeline_freshness": {
                "newest_ingested_at": (
                    newest_ingest.isoformat() if newest_ingest else None
                ),
                "age_hours": round(ingest_age, 1) if ingest_age is not None else None,
                "stalled": stalled,
            },
            # Not derived from any row timestamp -- see SOURCE_DATA_VINTAGE.
            "source_data_vintage": dict(SOURCE_DATA_VINTAGE),
        }
        if reasons:
            result["reason"] = "; ".join(reasons)
            # Loud, because this is the case that used to be indistinguishable
            # from working.
            log.warning(
                "[esg_aggregator] degraded: %s (computed=%d written=%d examined=%d)",
                result["reason"], computed, written, examined,
            )
        else:
            log.info(
                "[esg_aggregator] %s index: %d regions computed, %d changed, "
                "%d observations; vintage %s",
                SCORE_KIND, computed, written, examined,
                ", ".join(f"{k}={v}" for k, v in SOURCE_DATA_VINTAGE.items()),
            )
        return result
    except Exception as e:
        db.rollback()
        log.exception("[esg_aggregator] failed: %s", e)
        return {"status": "error", "error": str(e)}
    finally:
        db.close()
