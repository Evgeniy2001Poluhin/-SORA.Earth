from __future__ import annotations
import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import asyncio, logging, os
import asyncpg
from app.ingesters.openaq import OpenAQIngester
from app.ingesters.sber_veb_baseline import SberVebBaselineIngester

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("runner")

DSN = os.environ.get("DATABASE_URL") or "postgresql://sora:changeme_strong_password@localhost:5432/sora_earth"

async def write_signals(pool, signals):
    if not signals:
        return 0
    rows = [
        (s.region_code, s.source, s.metric, s.value, s.unit, s.observed_at,
         json.dumps(s.metadata) if s.metadata else None)
        for s in signals
    ]
    async with pool.acquire() as c:
        await c.executemany(
            """INSERT INTO raw_signals
               (region_code, source, metric, value, unit, observed_at, metadata)
               VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)""",
            rows,
        )
    return len(signals)

async def main():
    pool = await asyncpg.create_pool(DSN)
    for cls in (OpenAQIngester, SberVebBaselineIngester):
        ing = cls()
        log.info(f"running {ing.name}")
        signals = await ing.fetch_with_retry()
        n = await write_signals(pool, signals)
        log.info(f"[{ing.name}] wrote {n} signals")
    await pool.close()

if __name__ == "__main__":
    asyncio.run(main())
