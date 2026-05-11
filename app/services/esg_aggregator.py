"""Silver layer: aggregate region_signals -> region_esg_scores."""
from __future__ import annotations
import logging
from collections import defaultdict
from sqlalchemy import desc

from app.database import SessionLocal, RegionSignal, RegionESGScore

log = logging.getLogger(__name__)


def _clip(v, lo=0, hi=100):
    return max(lo, min(hi, v))


def _norm(v, lo, hi):
    if v is None:
        return None
    return _clip(100 * (v - lo) / (hi - lo))


def _latest_by_region(db):
    result = defaultdict(dict)
    rows = db.query(RegionSignal).order_by(desc(RegionSignal.observed_at)).all()
    for r in rows:
        key = f"{r.source}:{r.metric}"
        if key not in result[r.region_code]:
            result[r.region_code][key] = (r.value, r.source)
    return result


def _get(metrics, key):
    t = metrics.get(key)
    return t[0] if t else None


def _compute_one(metrics):
    pm25 = _get(metrics, "openaq:pm25_ugm3")
    e_openaq = (100 - _norm(pm25, 0, 50)) if pm25 is not None else None

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

    env = e_openaq if e_openaq is not None else base
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


def recalc_all_regions():
    db = SessionLocal()
    try:
        latest = _latest_by_region(db)
        updated = 0
        for region_code, metrics in latest.items():
            scores = _compute_one(metrics)
            if not scores:
                continue
            row = db.query(RegionESGScore).filter_by(region_code=region_code).first()
            if row:
                for k, v in scores.items():
                    setattr(row, k, v)
            else:
                row = RegionESGScore(region_code=region_code, **scores)
                db.add(row)
            updated += 1
        db.commit()
        log.info("[esg_aggregator] updated %d regions", updated)
        return {"status": "ok", "regions_updated": updated}
    except Exception as e:
        db.rollback()
        log.exception("[esg_aggregator] failed: %s", e)
        return {"status": "error", "error": str(e)}
    finally:
        db.close()
