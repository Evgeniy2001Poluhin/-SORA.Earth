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

# How old the newest observation may be before a run is degraded rather than a
# success. The sources behind REQUIRED_METRICS publish daily, so this allows
# one missed day before saying so.
_MAX_AGE_HOURS = float(os.getenv("SORA_AGGREGATOR_MAX_AGE_HOURS", "48"))


def _clip(v, lo=0, hi=100):
    return max(lo, min(hi, v))


def _norm(v, lo, hi):
    if v is None:
        return None
    return _clip(100 * (v - lo) / (hi - lo))


def _latest_by_region(db):
    """Newest valid observation per (region, source, indicator).

    Returns (metrics_by_region, newest_event_time, rows_examined).

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
            EnvironmentalObservation.event_time,
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
    newest = None
    examined = 0

    for region_id, source, indicator, value, event_time, _rn in db.execute(
        select(ranked).where(ranked.c.rn == 1)
    ):
        examined += 1
        result[region_id][f"{source}:{indicator}"] = (value, source)
        if event_time is not None and (newest is None or event_time > newest):
            newest = event_time

    return result, newest, examined


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


def recalc_all_regions(max_age_hours: float | None = None):
    """Recompute every region's score from the newest valid observations.

    The return value distinguishes three outcomes rather than two, following
    the rule already established for the environmental quality job (#56, #69):
    a window with nothing in it must not look like a window that was
    aggregated successfully.

    `regions_written` counts rows the database actually changed, which is not
    the same as `regions_computed`. When the source data has not moved, the
    recomputed values are identical, SQLAlchemy emits no UPDATE, and the
    column's `onupdate=func.now()` never fires -- so `updated_at` keeps meaning
    "when the value last changed" and says nothing about whether anything ran.
    That distinction is the reason this job reports staleness itself instead of
    leaving it to be inferred from a timestamp.
    """
    limit = _MAX_AGE_HOURS if max_age_hours is None else max_age_hours
    db = SessionLocal()
    try:
        latest, newest, examined = _latest_by_region(db)

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

        age = _age_hours(newest)
        reasons = []
        if examined == 0:
            reasons.append("no valid observations for any required metric")
        if age is not None and age > limit:
            reasons.append(
                f"newest observation is {age:.1f}h old, limit {limit:.0f}h"
            )

        result = {
            "status": "degraded" if reasons else "success",
            "regions_computed": computed,
            "regions_written": written,
            "observations_examined": examined,
            "newest_observation": newest.isoformat() if newest else None,
            "newest_age_hours": round(age, 1) if age is not None else None,
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
                "[esg_aggregator] computed %d regions, %d changed, %d observations",
                computed, written, examined,
            )
        return result
    except Exception as e:
        db.rollback()
        log.exception("[esg_aggregator] failed: %s", e)
        return {"status": "error", "error": str(e)}
    finally:
        db.close()
