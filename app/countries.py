"""
Supported countries for ESG evaluation.

This dict is the single source of truth for which countries a project may name.
GET /api/v1/countries serves exactly these keys.
Each country has a region (continent) and coordinates for mapping.
"""

COUNTRIES = {
    "Afghanistan": {"lat": 33.9, "lon": 67.7, "region": "Asia"},
    "Albania": {"lat": 41.2, "lon": 20.2, "region": "Europe"},
    "Algeria": {"lat": 28.0, "lon": 1.7, "region": "Africa"},
    "Argentina": {"lat": -38.4, "lon": -63.6, "region": "South America"},
    "Australia": {"lat": -25.3, "lon": 133.8, "region": "Oceania"},
    "Austria": {"lat": 47.5, "lon": 14.6, "region": "Europe"},
    "Sweden": {"lat": 60.1, "lon": 18.6, "region": "Europe"},
    "Norway": {"lat": 60.5, "lon": 8.5, "region": "Europe"},
    "Denmark": {"lat": 56.3, "lon": 9.5, "region": "Europe"},
    "Finland": {"lat": 61.9, "lon": 25.7, "region": "Europe"},
    "Netherlands": {"lat": 52.1, "lon": 5.3, "region": "Europe"},
    "Switzerland": {"lat": 46.8, "lon": 8.2, "region": "Europe"},
    "Brazil": {"lat": -14.2, "lon": -51.9, "region": "South America"},
    "Canada": {"lat": 56.1, "lon": -106.3, "region": "North America"},
    "China": {"lat": 35.9, "lon": 104.2, "region": "Asia"},
    "France": {"lat": 46.2, "lon": 2.2, "region": "Europe"},
    "Germany": {"lat": 51.2, "lon": 10.5, "region": "Europe"},
    "India": {"lat": 20.6, "lon": 79.0, "region": "Asia"},
    "Italy": {"lat": 41.9, "lon": 12.6, "region": "Europe"},
    "Japan": {"lat": 36.2, "lon": 138.3, "region": "Asia"},
    "Mexico": {"lat": 23.6, "lon": -102.6, "region": "North America"},
    "Nigeria": {"lat": 9.1, "lon": 8.7, "region": "Africa"},
    "Russia": {"lat": 55.75, "lon": 37.62, "region": "Europe"},
    "South Africa": {"lat": -30.6, "lon": 22.9, "region": "Africa"},
    "Spain": {"lat": 40.5, "lon": -3.7, "region": "Europe"},
    "United Kingdom": {"lat": 55.4, "lon": -3.4, "region": "Europe"},
    "United States": {"lat": 37.1, "lon": -95.7, "region": "North America"},
}


def check_country(value: str) -> str:
    """
    Validate that a country name is supported.

    Raises ValueError with the list of supported countries if the value
    is not in COUNTRIES. Returns the value unchanged if it is supported.

    This check is exact-match: "USA" is refused because it has no region here,
    and until this check existed it was scored with European multipliers.
    """
    if value not in COUNTRIES:
        supported = sorted(COUNTRIES.keys())
        raise ValueError(
            f"Unknown country '{value}'. "
            f"Supported countries: {', '.join(supported)}"
        )
    return value
