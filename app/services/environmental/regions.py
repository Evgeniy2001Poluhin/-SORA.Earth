"""Declared point sets for the environmental sources.

Read by the Open-Meteo ingester (which fetches these points) and by observation
coverage (which expects observations from each of them on each day). The
scheduler runs coverage, so this module imports only the standard library: it
must not import app.api or app.auth. tests/test_scheduler_imports_without_jwt_secret.py
pins that for the scheduler's import closure.
"""
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class DeclaredPoint:
    code: str
    lat: float
    lon: float
    #: First UTC day the point is expected to supply observations. Coverage
    #: expects nothing on earlier days, so declaring a point later does not
    #: manufacture gaps in the history before it was declared.
    since: date


#: Observations for the Open-Meteo points began to be persisted to
#: environmental_observations on this day, when the ingester runner moved to
#: persist_environmental_observations (#116). The point list itself was written
#: into the ingester on 2026-07-18, but before this day nothing reached the table
#: the coverage query reads. app/services/esg_aggregator.py records the move.
OPENMETEO_SINCE = date(2026, 7, 30)

#: The point set of docs/M3_FORECAST_DECLARATION.md section 2,
#: `openmeteo-points-v1`: 21 capitals, in the order the ingester has always
#: fetched them. Changing the membership is a version bump under that declaration.
OPENMETEO_POINTS_V1 = (
    # Europe
    DeclaredPoint("DEU", 52.5200, 13.4050, OPENMETEO_SINCE),   # Berlin
    DeclaredPoint("GBR", 51.5074, -0.1278, OPENMETEO_SINCE),   # London
    DeclaredPoint("FRA", 48.8566, 2.3522, OPENMETEO_SINCE),    # Paris
    DeclaredPoint("ESP", 40.4168, -3.7038, OPENMETEO_SINCE),   # Madrid
    DeclaredPoint("ITA", 41.9028, 12.4964, OPENMETEO_SINCE),   # Rome
    DeclaredPoint("NLD", 52.3676, 4.9041, OPENMETEO_SINCE),    # Amsterdam
    DeclaredPoint("POL", 52.2297, 21.0122, OPENMETEO_SINCE),   # Warsaw
    # Asia
    DeclaredPoint("IND", 28.6139, 77.2090, OPENMETEO_SINCE),   # New Delhi
    DeclaredPoint("CHN", 39.9042, 116.4074, OPENMETEO_SINCE),  # Beijing
    DeclaredPoint("JPN", 35.6762, 139.6503, OPENMETEO_SINCE),  # Tokyo
    DeclaredPoint("KOR", 37.5665, 126.9780, OPENMETEO_SINCE),  # Seoul
    DeclaredPoint("IDN", -6.2088, 106.8456, OPENMETEO_SINCE),  # Jakarta
    # Americas
    DeclaredPoint("USA", 38.9072, -77.0369, OPENMETEO_SINCE),  # Washington DC
    DeclaredPoint("BRA", -15.7975, -47.8919, OPENMETEO_SINCE),  # Brasilia
    DeclaredPoint("MEX", 19.4326, -99.1332, OPENMETEO_SINCE),  # Mexico City
    DeclaredPoint("CAN", 45.4215, -75.6972, OPENMETEO_SINCE),  # Ottawa
    # Africa
    DeclaredPoint("NGA", 9.0579, 7.4951, OPENMETEO_SINCE),     # Abuja
    DeclaredPoint("ZAF", -25.7479, 28.2293, OPENMETEO_SINCE),  # Pretoria
    DeclaredPoint("KEN", -1.2921, 36.8219, OPENMETEO_SINCE),   # Nairobi
    # Russia
    DeclaredPoint("RU-MOW", 55.7558, 37.6173, OPENMETEO_SINCE),  # Moscow
    DeclaredPoint("RU-SPE", 59.9343, 30.3351, OPENMETEO_SINCE),  # St. Petersburg
)

#: (source, indicator) -> the declared points a complete day requires. A pair
#: absent from this mapping has no expected grid, and coverage answers "not
#: supported" for it instead of a count. Only temperature is declared: the M3
#: declaration measures that indicator, and the other Open-Meteo variables are
#: not confirmed to arrive for every point on every run.
COVERAGE_REGISTRY = {
    ("openmeteo", "temperature"): OPENMETEO_POINTS_V1,
}
