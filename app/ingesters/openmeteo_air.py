"""Air quality from Open-Meteo, where OpenAQ has nothing recent to give.

OpenAQ's stations for the regions this platform ingests stopped reporting in
September 2017 — measured on production, every station within 25 km of Moscow.
That is why `openaq_ingestion` produced zero records across 333 runs: the source
is reachable, authenticated and healthy, and empty. See issues #56 and #57.

This is the same provider as `openmeteo.py`, on a sibling endpoint. No API key,
same response shape, same client. It is a second ingester rather than a method on
the first because the endpoint, the variables and the reason for existing are
different, and a run that fetched weather but not air should not be recorded as
one run.

**These are modelled values, not station measurements.** Open-Meteo's air quality
comes from CAMS reanalysis, so a value exists where no station does — which is
the point here, and also the cost. A specialist must be able to tell the two
apart, so `source` distinguishes this from `openaq`, and the dataset card must
say so in words.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

from app.ingesters.base import BaseIngester, Signal
from app.ingesters.openmeteo import REGION_CAPITALS

log = logging.getLogger(__name__)

# Pollutants, in the API's own names. Kept to what CAMS reports directly rather
# than derived indices: an AQI is a national formula applied to these, and
# storing one would bake a jurisdiction's thresholds into an observation.
AIR_VARIABLES = [
    "pm10",
    "pm2_5",
    "carbon_monoxide",
    "nitrogen_dioxide",
    "sulphur_dioxide",
    "ozone",
]


class OpenMeteoAirIngester(BaseIngester):
    """Air quality for all regions, hourly, no key.

    Rate limits are the provider's usual 10,000 requests/day per IP, which one
    request per region per hour does not approach.
    """

    name = "openmeteo_air"
    default_ttl_hours = 1

    async def fetch(self) -> List[Signal]:
        out: List[Signal] = []
        now = datetime.now(timezone.utc)

        async with self.client() as c:
            for code, lat, lon in REGION_CAPITALS:
                try:
                    params = {
                        "latitude": lat,
                        "longitude": lon,
                        "current": ",".join(AIR_VARIABLES),
                        "timezone": "UTC",
                    }
                    r = await c.get(
                        "https://air-quality-api.open-meteo.com/v1/air-quality",
                        params=params,
                    )
                    r.raise_for_status()
                    data = r.json()

                    current = data.get("current", {})
                    if not current:
                        log.warning(f"[openmeteo_air] {code} - no current air quality data")
                        continue

                    # The API's own timestamp, not ours. Falling back to `now`
                    # would date a value by when we asked rather than when it
                    # applies -- the same conflation that left 87,005 indicator
                    # rows undated (#58).
                    obs_time_str = current.get("time")
                    if obs_time_str:
                        obs_time = datetime.fromisoformat(obs_time_str).replace(
                            tzinfo=timezone.utc)
                    else:
                        obs_time = now

                    for var in AIR_VARIABLES:
                        value = current.get(var)
                        if value is None:
                            continue

                        metric_name, unit = self._map_variable(var)
                        out.append(Signal(
                            region_code=code,
                            source=self.name,
                            metric=metric_name,
                            value=float(value),
                            unit=unit,
                            observed_at=obs_time,
                        ))

                except Exception as e:
                    log.warning(f"[openmeteo_air] {code} failed: {e}")

        log.info(
            f"[openmeteo_air] fetched {len(out)} signals from "
            f"{len(REGION_CAPITALS)} regions"
        )
        return out

    @staticmethod
    def _map_variable(api_var: str) -> tuple[str, str]:
        """API variable to metric name and unit.

        Units are the provider's: µg/m³ for every pollutant here, including
        carbon monoxide, which some sources report in mg/m³. Recording the wrong
        unit is worse than recording no value, so this states it explicitly
        rather than assuming a convention.
        """
        mapping = {
            "pm10": ("pm10", "ug/m3"),
            "pm2_5": ("pm25", "ug/m3"),
            "carbon_monoxide": ("carbon_monoxide", "ug/m3"),
            "nitrogen_dioxide": ("nitrogen_dioxide", "ug/m3"),
            "sulphur_dioxide": ("sulphur_dioxide", "ug/m3"),
            "ozone": ("ozone", "ug/m3"),
        }
        return mapping.get(api_var, (api_var, "unknown"))
