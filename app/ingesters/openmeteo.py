"""Open-Meteo weather data ingester.

Fetches current weather and forecast data for environmental analysis.
Open-Meteo is a free weather API with no API key required.

API: https://open-meteo.com/
Docs: https://open-meteo.com/en/docs
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import List

from app.ingesters.base import BaseIngester, Signal
from app.services.environmental.regions import OPENMETEO_POINTS_V1

log = logging.getLogger(__name__)

# Same regions as OpenAQ for consistency. The declared set (codes, coordinates and
# order) lives in app/services/environmental/regions.py, shared with observation
# coverage. This is its (code, lat, lon) view, in the same order as before.
REGION_CAPITALS = [(p.code, p.lat, p.lon) for p in OPENMETEO_POINTS_V1]

# Weather variables to fetch
# https://open-meteo.com/en/docs#current=temperature_2m,relative_humidity_2m,...
WEATHER_VARIABLES = [
    "temperature_2m",         # Temperature at 2m height (°C)
    "relative_humidity_2m",   # Relative humidity at 2m (%)
    "apparent_temperature",   # Feels-like temperature (°C)
    "precipitation",          # Precipitation (mm)
    "rain",                   # Rain (mm)
    "pressure_msl",           # Mean sea level pressure (hPa)
    "surface_pressure",       # Surface pressure (hPa)
    "wind_speed_10m",         # Wind speed at 10m (km/h)
    "wind_direction_10m",     # Wind direction at 10m (degrees)
    "wind_gusts_10m",         # Wind gusts at 10m (km/h)
]


class OpenMeteoIngester(BaseIngester):
    """Ingester for Open-Meteo weather data.

    Fetches current weather observations for climate/environmental analysis.
    No API key required - free for research/non-commercial use.

    Rate limits: 10,000 requests/day per IP (reasonable for hourly polling).
    """
    name = "openmeteo"
    default_ttl_hours = 1  # Weather data changes hourly

    async def fetch(self) -> List[Signal]:
        """Fetch current weather for all regions.

        Returns:
            List of Signal objects with weather measurements
        """
        out = []
        now = datetime.now(timezone.utc)

        async with self.client() as c:
            for code, lat, lon in REGION_CAPITALS:
                try:
                    # Open-Meteo API endpoint
                    # https://api.open-meteo.com/v1/forecast?latitude=52.52&longitude=13.41&current=...
                    params = {
                        "latitude": lat,
                        "longitude": lon,
                        "current": ",".join(WEATHER_VARIABLES),
                        "timezone": "UTC",  # Always use UTC for consistency
                    }

                    r = await c.get(
                        "https://api.open-meteo.com/v1/forecast",
                        params=params
                    )
                    r.raise_for_status()
                    data = r.json()

                    # Extract current weather
                    current = data.get("current", {})
                    if not current:
                        log.warning(f"[openmeteo] {code} - no current weather data")
                        continue

                    # Parse observation timestamp from API
                    obs_time_str = current.get("time")
                    if obs_time_str:
                        # Format: "2024-01-15T12:00" (ISO 8601 without timezone)
                        obs_time = datetime.fromisoformat(obs_time_str).replace(tzinfo=timezone.utc)
                    else:
                        obs_time = now

                    # Extract all weather variables
                    for var in WEATHER_VARIABLES:
                        value = current.get(var)
                        if value is None:
                            continue

                        # Map variable names to our metric names
                        metric_name, unit = self._map_variable(var)

                        out.append(Signal(
                            region_code=code,
                            source=self.name,
                            metric=metric_name,
                            value=float(value),
                            unit=unit,
                            observed_at=obs_time,
                            # The point this reading was requested for (#84).
                            #
                            # These are the coordinates already sent to the API
                            # a few lines above, so the row records where the
                            # value came from rather than leaving it to be
                            # looked up in REGION_CAPITALS by whoever reads it
                            # later -- a lookup that answers with wherever the
                            # constant points *now*, not where this request
                            # went.
                            #
                            # It is the region's capital, not the region: a
                            # weather reading is taken at a point, and calling
                            # it "the region" would be a wider claim than the
                            # source makes.
                            metadata={"latitude": lat, "longitude": lon},
                        ))

                except Exception as e:
                    log.warning(f"[openmeteo] {code} failed: {e}")

        log.info(f"[openmeteo] fetched {len(out)} signals from {len(REGION_CAPITALS)} regions")
        return out

    @staticmethod
    def _map_variable(api_var: str) -> tuple[str, str]:
        """Map Open-Meteo variable names to our metric names and units.

        Args:
            api_var: Variable name from Open-Meteo API

        Returns:
            Tuple of (metric_name, unit)
        """
        # Map API variable names to standardized metric names
        mapping = {
            "temperature_2m": ("temperature", "celsius"),
            "relative_humidity_2m": ("humidity", "percent"),
            "apparent_temperature": ("heat_index", "celsius"),
            "precipitation": ("precipitation", "mm"),
            "rain": ("rain", "mm"),
            "pressure_msl": ("pressure_msl", "hPa"),
            "surface_pressure": ("pressure_surface", "hPa"),
            "wind_speed_10m": ("wind_speed", "km/h"),
            "wind_direction_10m": ("wind_direction", "degrees"),
            "wind_gusts_10m": ("wind_gusts", "km/h"),
        }

        return mapping.get(api_var, (api_var, "unknown"))
