"""Real ESG data from World Bank API + static benchmarks."""
import httpx
import logging
from functools import lru_cache
from typing import Optional, Dict, List
from app.country_benchmarks import BENCHMARKS, GLOBAL_AVG

logger = logging.getLogger("sora")

WB_BASE = "https://api.worldbank.org/v2"

INDICATORS = {
    "co2_per_capita": "EN.ATM.CO2E.PC",
    "renewable_share": "EG.FEC.RNEW.ZS",
    "life_expectancy": "SP.DYN.LE00.IN",
}

COUNTRY_ISO3 = {
    "Afghanistan": "AFG", "Albania": "ALB", "Algeria": "DZA",
    "Argentina": "ARG", "Australia": "AUS", "Austria": "AUT",
    "Brazil": "BRA", "Canada": "CAN", "China": "CHN",
    "Denmark": "DNK", "France": "FRA", "Germany": "DEU",
    "India": "IND", "Italy": "ITA", "Japan": "JPN",
    "Mexico": "MEX", "Netherlands": "NLD", "Nigeria": "NGA",
    "Norway": "NOR", "Russia": "RUS", "South Africa": "ZAF",
    "South Korea": "KOR", "Spain": "ESP", "Sweden": "SWE",
    "Switzerland": "CHE", "Turkey": "TUR",
    "United Kingdom": "GBR", "United States": "USA",
    "Indonesia": "IDN", "Saudi Arabia": "SAU",
}

_live_cache: Dict[str, dict] = {}
_refresh_status = {"last_refresh": None, "countries_refreshed": 0, "status": "idle"}


def _fetch_indicator(iso3: str, indicator: str) -> Optional[float]:
    url = f"{WB_BASE}/country/{iso3}/indicator/{indicator}?format=json&per_page=10&mrv=1"
    try:
        resp = httpx.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if len(data) >= 2 and data[1]:
            for entry in data[1]:
                if entry.get("value") is not None:
                    return round(float(entry["value"]), 2)
    except Exception as e:
        logger.warning(f"World Bank API error for {iso3}/{indicator}: {e}")
    return None


def get_country_esg_realtime(country_name: str) -> Optional[Dict]:
    if country_name in _live_cache:
        return _live_cache[country_name]
    iso3 = COUNTRY_ISO3.get(country_name)
    if not iso3:
        return None
    result = {"country": country_name, "iso3": iso3, "source": "World Bank API"}
    for key, indicator_code in INDICATORS.items():
        val = _fetch_indicator(iso3, indicator_code)
        if val is not None:
            result[key] = val
    if len(result) <= 3:
        return None
    return result


def get_country_context(country_name: str) -> Optional[Dict]:
    return get_country_esg_realtime(country_name)


def refresh_all_countries() -> Dict:
    results = {}
    for name in COUNTRY_ISO3:
        data = get_country_esg_realtime(name)
        if data:
            results[name] = data
    return {"fetched": len(results), "total": len(COUNTRY_ISO3), "countries": results}


def refresh_live_data() -> Dict:
    from datetime import datetime
    _refresh_status["status"] = "running"
    result = refresh_all_countries()
    # cache cleared via _live_cache below
    get_country_esg_realtime.cache_clear()
    _live_cache.update(result.get("countries", {}))
    _refresh_status["last_refresh"] = datetime.now().isoformat()
    _refresh_status["countries_refreshed"] = result["fetched"]
    _refresh_status["status"] = "idle"
    return result


def get_refresh_status() -> Dict:
    all_countries = get_supported_countries()
    return {
        "static_countries": len(all_countries),
        "live_cached": len(_live_cache),
        "running": _refresh_status["status"] == "running",
        **_refresh_status,
    }


def get_supported_countries() -> List[str]:
    all_names = set(COUNTRY_ISO3.keys()) | set(BENCHMARKS.keys())
    return sorted(all_names)


def get_merged_country_data(name: str) -> Optional[Dict]:
    bench = BENCHMARKS.get(name)
    live = _live_cache.get(name)
    if not bench and not live:
        return None
    result = {}
    if bench:
        result.update(bench)
    if live:
        result["live"] = live
    return result


def get_all_countries_merged() -> Dict[str, dict]:
    all_names = get_supported_countries()
    result = {}
    for name in all_names:
        merged = get_merged_country_data(name)
        if merged:
            result[name] = merged
        else:
            result[name] = {"source": "name_only"}
    return result
