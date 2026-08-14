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

log = logging.getLogger(__name__)

# Same regions as OpenAQ for consistency
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
    ("RU-MOW", 55.7558, 37.6173),  # Moscow
    ("RU-SPE", 59.9343, 30.3351),  # St. Petersburg
]

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
