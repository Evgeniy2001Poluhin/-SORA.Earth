from __future__ import annotations
import logging, os, re, asyncio
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter
import asyncpg

log = logging.getLogger("map_russia")
router = APIRouter(prefix="/api/v1/map", tags=["map"])

DSN = os.environ.get("DATABASE_URL") or "postgresql://sora:changeme_strong_password@localhost:5432/sora_earth"

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
_MOCK = [
    {"code":"RU-MOW","name":"Moscow","capital":"Moscow","district":"CFO","lat":55.7558,"lon":37.6173,"population":13010112,
     "esg":{"score":89.0,"e_score":87.0,"s_score":88.0,"g_score":90.0}},
    {"code":"RU-SPE","name":"SPb","capital":"SPb","district":"SZFO","lat":59.9343,"lon":30.3351,"population":5601911,
     "esg":{"score":77.0,"e_score":75.0,"s_score":78.0,"g_score":78.0}},
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

async def _load_from_db():
    pool = await _get_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT region_code, e_score, s_score, g_score, score, "
                "confidence, sources_used, computed_at "
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
