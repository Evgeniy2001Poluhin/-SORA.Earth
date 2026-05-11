from __future__ import annotations
import asyncio, logging, os, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import asyncpg

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("builder")
DSN = os.environ.get("DATABASE_URL") or "postgresql://sora:changeme_strong_password@localhost:5432/sora_earth"

ALL_SOURCES = ["openaq", "sber_veb_baseline"]

SQL_LAST = "SELECT DISTINCT ON (region_code, source, metric) region_code, source, metric, value, observed_at FROM raw_signals ORDER BY region_code, source, metric, observed_at DESC"

SQL_INS = "INSERT INTO regional_esg_snapshot (region_code, e_score, s_score, g_score, score, confidence, sources_used, sources_missing) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)"

async def main():
    pool = await asyncpg.create_pool(DSN)
    async with pool.acquire() as c:
        rows = await c.fetch(SQL_LAST)
        buckets = {}
        for r in rows:
            key = r["source"] + ":" + r["metric"]
            buckets.setdefault(r["region_code"], {})[key] = (r["value"], r["source"])

        snapshots = []
        for code, metrics in buckets.items():
            sources_used = sorted({v[1] for v in metrics.values()})
            missing = sorted(set(ALL_SOURCES) - set(sources_used))
            conf = round(len(sources_used) / len(ALL_SOURCES), 2)

            bl = metrics.get("sber_veb_baseline:esg_index_baseline")
            base_score = bl[0] if bl else 50.0

            pm25 = metrics.get("openaq:pm25_ugm3")
            no2 = metrics.get("openaq:no2_ugm3")
            e_adjust = 0.0
            if pm25:
                e_adjust += max(-15, min(5, 15 - pm25[0] * 0.5))
            if no2:
                e_adjust += max(-10, min(3, 10 - no2[0] * 0.2))

            e_score = max(0, min(100, base_score + e_adjust))
            s_score = base_score
            g_score = base_score
            score = round(0.4 * e_score + 0.3 * s_score + 0.3 * g_score, 1)

            snapshots.append((code, e_score, s_score, g_score, score, conf, sources_used, missing))

        await c.execute("TRUNCATE regional_esg_snapshot")
        await c.executemany(SQL_INS, snapshots)
        log.info(f"wrote {len(snapshots)} snapshots")
    await pool.close()

if __name__ == "__main__":
    asyncio.run(main())
