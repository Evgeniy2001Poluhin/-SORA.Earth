"""Air quality from Open-Meteo, for the regions where OpenAQ has gone silent.

OpenAQ's stations around these coordinates stopped reporting in September 2017.
The API is reachable, authenticated and healthy, and returns nothing: measured on
production, `openaq_ingestion` has 398 runs, 0 records, and recorded `success`
every time (#56, #57).

This is a sibling endpoint of the weather ingester already in use -- same
service, same response shape, same client, no API key -- so it is a second
source on an existing integration rather than a new one built from nothing.

**These values are modelled, not measured.** They come from the CAMS reanalysis,
which is why they exist where the stations are dead. That belongs in plain words
here and in the source register, not buried: a specialist looking at a number
has to be able to tell which of the two it is, and `measurement_kind` on every
signal is how. Conflating a model with an instrument is a worse failure than
having no data, because it is invisible.

API: https://open-meteo.com/en/docs/air-quality-api
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

from app.ingesters.base import BaseIngester, Signal
from app.ingesters.openmeteo import REGION_CAPITALS

log = logging.getLogger(__name__)

ENDPOINT = "https://air-quality-api.open-meteo.com/v1/air-quality"

# The six the platform already reasons about, in the units the API returns.
POLLUTANTS = {
    "pm10": "µg/m³",
    "pm2_5": "µg/m³",
    "nitrogen_dioxide": "µg/m³",
    "sulphur_dioxide": "µg/m³",
    "ozone": "µg/m³",
    "carbon_monoxide": "µg/m³",
}


class OpenMeteoAirQualityIngester(BaseIngester):
    name = "openmeteo_air_quality"
    default_ttl_hours = 6

    async def fetch(self) -> List[Signal]:
        out: List[Signal] = []
        now = datetime.now(timezone.utc)

        async with self.client() as c:
            for code, lat, lon in REGION_CAPITALS:
                try:
                    r = await c.get(
                        ENDPOINT,
                        params={
                            "latitude": lat,
                            "longitude": lon,
                            "current": ",".join(POLLUTANTS),
                            "timezone": "UTC",
                        },
                    )
                    r.raise_for_status()
                    current = (r.json() or {}).get("current") or {}
                except Exception as e:
                    # One region failing must not cost the other twenty. The
                    # empty result is what the run is classified on, so a region
                    # lost here shows up as fewer records rather than as a
                    # success that quietly covered less ground.
                    log.warning("[%s] %s: %s", self.name, code, e)
                    continue

                if not current:
                    log.warning("[%s] %s: no current block in the response", self.name, code)
                    continue

                observed_at = _parse_time(current.get("time"), now, code)

                for pollutant, unit in POLLUTANTS.items():
                    value = current.get(pollutant)
                    if value is None:
                        continue
                    out.append(
                        Signal(
                            region_code=code,
                            source=self.name,
                            metric=pollutant,
                            value=float(value),
                            unit=unit,
                            observed_at=observed_at,
                            metadata={
                                # The honest cost of this source, on every row.
                                "measurement_kind": "modelled",
                                "model": "CAMS reanalysis via Open-Meteo",
                                "latitude": lat,
                                "longitude": lon,
                            },
                        )
                    )

        log.info("[%s] %d signals across %d regions",
                 self.name, len(out), len({s.region_code for s in out}))
        return out


def _parse_time(raw, fallback: datetime, code: str) -> datetime:
    """The period the value describes, or the fetch time if the source omits it.

    Never silently the fetch time when the source did state one: that is the
    defect #58 records for country_indicator_history, where a fetch timestamp
    stood in for an observation period and nothing in the database could tell
    the difference afterwards.
    """
    if not raw:
        log.warning("[openmeteo_air_quality] %s: no observation time; using fetch time", code)
        return fallback
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        log.warning("[openmeteo_air_quality] %s: unparseable time %r; using fetch time", code, raw)
        return fallback
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
