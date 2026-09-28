from __future__ import annotations
import logging, os, re, asyncio
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter
import asyncpg

log = logging.getLogger("map_russia")
router = APIRouter(prefix="/api/v1/map", tags=["map"])

DSN = (os.environ.get("DATABASE_URL") or "postgresql://sora:changeme_strong_password@localhost:5432/sora_earth").replace("+psycopg", "").replace("postgresql+asyncpg", "postgresql")

# ---------- parse russia_regions.ts once ----------
_TS_PATH = Path(__file__).resolve().parent.parent.parent / "web/src/data/russia_regions.ts"
_RX = re.compile(
    r'code:\s*"([^"]+)",\s*name:\s*"([^"]+)",\s*capital:\s*"([^"]+)",'
    r'\s*district:\s*"([^"]+)",\s*lat:\s*([\d.\-]+),\s*lon:\s*([\d.\-]+),'
    r'\s*population:\s*(\d+)'
)

def _load_regions():
    if not _TS_PATH.exists():
        return {}
    out = {}
    for m in _RX.finditer(_TS_PATH.read_text()):
        out[m.group(1)] = {
            "code": m.group(1),
            "name": m.group(2),
            "capital": m.group(3),
            "district": m.group(4),
            "lat": float(m.group(5)),
            "lon": float(m.group(6)),
            "population": int(m.group(7)),
        }
    return out

_REGIONS_META = _load_regions()
log.info(f"[map_russia] loaded {len(_REGIONS_META)} region metadata entries")

# ---------- fallback mock ----------
#
# Served when both database readers come back empty, with `source:
# "mock-esg-v1-fallback"` in the response -- honest at the API, and the
# interface dropped it: `useRussiaMap` takes `j.regions` and discards `source`,
# so Moscow appeared at 89.0 with nothing to say the number was invented. This
# platform has already served invented ESG scores to production for weeks with
# every health check passing, which is why these rows now declare what they are
# and the card repeats it.
_MOCK = [
    {"code":"RU-MOW","name":"Moscow","capital":"Moscow","district":"CFO","lat":55.7558,"lon":37.6173,"population":13010112,
     "esg":{"score":89.0,"e_score":87.0,"s_score":88.0,"g_score":90.0},
     "score_kind":"mock","score_vintage":None,"stale_since":None},
    {"code":"RU-SPE","name":"SPb","capital":"SPb","district":"SZFO","lat":59.9343,"lon":30.3351,"population":5601911,
     "esg":{"score":77.0,"e_score":75.0,"s_score":78.0,"g_score":78.0},
     "score_kind":"mock","score_vintage":None,"stale_since":None},
]

_pool = None
async def _get_pool():
    global _pool
    if _pool is None:
        try:
            _pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4)
        except Exception as e:
            log.warning(f"[map_russia] pool init failed: {e}")
            _pool = None
    return _pool

def score_provenance() -> dict:
    """What kind of number the region score is, and which data it stands on.

    Read from the two modules that decide it, never written down here: the
    aggregator declares `SCORE_KIND` beside the formula, and the rosstat
    ingester declares the snapshot's reference period. A year typed into this
    file, or into the interface, is a figure that cannot stay true.

    The label existed and did not travel: `SCORE_KIND = "structural"` appeared
    only in the aggregator's own run result, so the card showed a 2024
    structural index as an ESG score with no indication of either.
    """
    kind = "structural"
    vintage = None
    try:
        from app.services.esg_aggregator import SCORE_KIND
        kind = SCORE_KIND
    except Exception:  # pragma: no cover - import guard, same style as below
        pass
    try:
        from app.ingesters.rosstat import PERIOD_END, PERIOD_START
        vintage = (str(PERIOD_START.year) if PERIOD_START.year == PERIOD_END.year
                   else f"{PERIOD_START.year}-{PERIOD_END.year}")
    except Exception:  # pragma: no cover
        pass
    return {"score_kind": kind, "score_vintage": vintage}


async def _load_from_db():
    pool = await _get_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT region_code, e_score, s_score, g_score, score, "
                "confidence, sources_used, computed_at, stale_since "
                "FROM regional_esg_snapshot"
            )
    except Exception as e:
        log.warning(f"[map_russia] DB read failed: {e}")
        return []
    regions = []
    for r in rows:
        meta = _REGIONS_META.get(r["region_code"], {"code": r["region_code"]})
        regions.append({
            **meta,
            "esg": {
                "score": float(r["score"] or 0),
                "e_score": float(r["e_score"] or 0),
                "s_score": float(r["s_score"] or 0),
                "g_score": float(r["g_score"] or 0),
            },
            "confidence": float(r["confidence"] or 0),
            "sources_used": list(r["sources_used"] or []),
            "updated_at": r["computed_at"].isoformat() if r["computed_at"] else None,
            "stale_since": r["stale_since"].isoformat() if r["stale_since"] else None,
            **score_provenance(),
        })
    return regions


def _load_from_sqlalchemy():
    """Fallback: read from SQLAlchemy RegionESGScore (Silver layer)."""
    try:
        from app.database import SessionLocal, RegionESGScore
    except Exception as e:
        log.warning(f"[map_russia] sqlalchemy model unavailable: {e}")
        return []
    db = SessionLocal()
    try:
        rows = db.query(RegionESGScore).all()
    finally:
        db.close()
    regions = []
    for r in rows:
        meta = _REGIONS_META.get(r.region_code, {"code": r.region_code})
        regions.append({
            **meta,
            "esg": {
                "score": float(r.total_score or 0),
                "e_score": float(r.env_score or 0),
                "s_score": float(r.social_score or 0),
                "g_score": float(r.gov_score or 0),
            },
            "confidence": float(r.confidence or 0),
            "sources_used": [],
            "sources_count": int(r.sources_count or 0),
            "signals_used": int(r.signals_used or 0),
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            "stale_since": r.stale_since.isoformat() if r.stale_since else None,
            **score_provenance(),
        })
    return regions

@router.get("/russia")
async def russia_regions():
    regions = await _load_from_db()
    source = "db-esg-v1-asyncpg"
    if not regions:
        regions = _load_from_sqlalchemy()
        source = "db-esg-v2-sqlalchemy"
    if not regions:
        regions = _MOCK
        source = "mock-esg-v1-fallback"
    return {
        "regions": regions,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "count": len(regions),
    }

@router.get("/russia/{region_code}")
async def region_detail(region_code: str):
    """Detailed region view: snapshot + inputs of the score."""
    pool = await _get_pool()
    if pool is None:
        return {"error": "DB unavailable", "region_code": region_code}

    meta = _REGIONS_META.get(region_code, {"code": region_code, "name": region_code})

    # Read the snapshot (asyncpg)
    snap = None
    try:
        async with pool.acquire() as c:
            snap = await c.fetchrow(
                "SELECT region_code, e_score, s_score, g_score, score, "
                "confidence, sources_used, ARRAY[]::text[] AS sources_missing, computed_at, "
                "NULL::text AS model_version, NULL::jsonb AS features, stale_since "
                "FROM regional_esg_snapshot WHERE region_code = $1",
                region_code,
            )
    except Exception as e:
        log.warning(f"[region_detail] DB read failed: {e}")
        return {"error": str(e), "region_code": region_code}

    # Read the inputs (SQLAlchemy in a thread). None, not [], when the read
    # fails: an empty list would render as "no inputs" and hide the failure.
    inputs = None
    try:
        from app.database import SessionLocal
        from app.services.esg_aggregator import required_inputs_for_region

        def _get_inputs():
            db = SessionLocal()
            try:
                return required_inputs_for_region(db, region_code)
            finally:
                db.close()

        inputs = await asyncio.to_thread(_get_inputs)
    except Exception as e:
        log.warning(f"[region_detail] inputs read failed: {e}")

    # "region not found" means: no snapshot row and every input read and missing.
    # A failed read is not evidence that the region does not exist.
    if snap is None and inputs is not None and all(inp["missing"] for inp in inputs):
        return {"error": "region not found", "region_code": region_code}

    esg = None
    if snap:
        esg = {
            "score": float(snap["score"] or 0),
            "e_score": float(snap["e_score"] or 0),
            "s_score": float(snap["s_score"] or 0),
            "g_score": float(snap["g_score"] or 0),
        }

    # Serialize inputs: datetimes as ISO strings; None stays None (read failed)
    inputs_serialized = None if inputs is None else []
    for inp in inputs or []:
        s = {**inp}
        if s.get("period_start"):
            s["period_start"] = s["period_start"].isoformat()
        if s.get("period_end"):
            s["period_end"] = s["period_end"].isoformat()
        if s.get("delivered_at"):
            s["delivered_at"] = s["delivered_at"].isoformat()
        inputs_serialized.append(s)

    return {
        "region": meta,
        "esg": esg,
        "confidence": float(snap["confidence"]) if snap and snap["confidence"] is not None else None,
        "sources_used": list(snap["sources_used"]) if snap and snap["sources_used"] else [],
        "sources_missing": list(snap["sources_missing"]) if snap and snap["sources_missing"] else [],
        "model_version": snap["model_version"] if snap else None,
        "computed_at": snap["computed_at"].isoformat() if snap and snap["computed_at"] else None,
        "stale_since": snap["stale_since"].isoformat() if snap and snap["stale_since"] else None,
        "features": dict(snap["features"]) if snap and snap["features"] else None,
        "inputs": inputs_serialized,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **score_provenance(),
    }

