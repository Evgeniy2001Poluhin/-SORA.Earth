from __future__ import annotations
import logging, os
from datetime import datetime, timezone
from app.ingesters.base import BaseIngester, Signal

log = logging.getLogger(__name__)

REGION_CAPITALS = [
    # Europe
    ("DEU", 52.5200, 13.4050),   # Berlin
    ("GBR", 51.5074, -0.1278),   # London
    ("FRA", 48.8566, 2.3522),    # Paris
    ("ESP", 40.4168, -3.7038),   # Madrid
    ("ITA", 41.9028, 12.4964),   # Rome
    ("NLD", 52.3676, 4.9041),    # Amsterdam
    ("POL", 52.2297, 21.0122),   # Warsaw
    # Asia
    ("IND", 28.6139, 77.2090),   # New Delhi
    ("CHN", 39.9042, 116.4074),  # Beijing
    ("JPN", 35.6762, 139.6503),  # Tokyo
    ("KOR", 37.5665, 126.9780),  # Seoul
    ("IDN", -6.2088, 106.8456),  # Jakarta
    # Americas
    ("USA", 38.9072, -77.0369),  # Washington DC
    ("BRA", -15.7975, -47.8919), # Brasilia
    ("MEX", 19.4326, -99.1332),  # Mexico City
    ("CAN", 45.4215, -75.6972),  # Ottawa
    # Africa
    ("NGA", 9.0579, 7.4951),     # Abuja
    ("ZAF", -25.7479, 28.2293),  # Pretoria
    ("KEN", -1.2921, 36.8219),   # Nairobi
    # Russia
    ("RU-MOW", 55.7558, 37.6173),
    ("RU-SPE", 59.9343, 30.3351),
]

PARAMETERS = ("pm25", "no2", "o3", "so2")


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
                        params={"coordinates": f"{lat},{lon}", "radius": 25000, "limit": 5},
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
                        param = (m.get("parameter") or {}).get("name", "")
                        val = m.get("value")
                        if param in PARAMETERS and val is not None:
                            out.append(Signal(
                                region_code=code,
                                source=self.name,
                                metric=f"{param}_ugm3",
                                value=float(val),
                                unit="ug/m3",
                                observed_at=now,
                            ))
                except Exception as e:
                    log.warning(f"[openaq] {code} failed: {e}")
        log.info(f"[openaq] fetched {len(out)} signals from {len(REGION_CAPITALS)} regions")
        return out
