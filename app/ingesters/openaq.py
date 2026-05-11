from __future__ import annotations
import logging, os
from datetime import datetime, timezone
from app.ingesters.base import BaseIngester, Signal

log = logging.getLogger(__name__)

REGION_CAPITALS = [
    ("RU-MOW", 55.7558, 37.6173),
    ("RU-SPE", 59.9343, 30.3351),
    ("RU-MOS", 55.8316, 37.3295),
    ("RU-LEN", 59.5624, 30.1276),
    ("RU-SA",  62.0283, 129.7319),
]

class OpenAQIngester(BaseIngester):
    name = "openaq"
    default_ttl_hours = 6

    async def fetch(self):
        key = os.environ.get("OPENAQ_API_KEY")
        if not key:
            log.info("[openaq] no OPENAQ_API_KEY set, skipping")
            return []
        out = []
        async with self.client() as c:
            c.headers["X-API-Key"] = key
            for code, lat, lon in REGION_CAPITALS:
                try:
                    r = await c.get(
                        "https://api.openaq.org/v3/locations",
                        params={"coordinates": f"{lat},{lon}", "radius": 25000, "limit": 10},
                    )
                    r.raise_for_status()
                    locs = r.json().get("results", [])
                    if not locs:
                        continue
                    loc_id = locs[0].get("id")
                    lr = await c.get(f"https://api.openaq.org/v3/locations/{loc_id}/latest")
                    lr.raise_for_status()
                    now = datetime.now(timezone.utc)
                    for m in lr.json().get("results", []):
                        param = (m.get("parameter") or {}).get("name")
                        val = m.get("value")
                        if param in ("pm25", "no2") and val is not None:
                            out.append(Signal(code, self.name, f"{param}_ugm3", float(val), "ug/m3", now))
                except Exception as e:
                    log.warning(f"[openaq] {code} failed: {e}")
        return out
