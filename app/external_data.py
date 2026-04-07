"""Real ESG data from World Bank API + static benchmarks with TTL cache."""
import httpx
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from app.country_benchmarks import BENCHMARKS, GLOBAL_AVG

logger = logging.getLogger("sora")

WB_BASE = "https://api.worldbank.org/v2"

INDICATORS = {
    "co2_per_capita":       "EN.ATM.CO2E.PC",
    "renewable_share":      "EG.FEC.RNEW.ZS",
    "life_expectancy":      "SP.DYN.LE00.IN",
    "gdp_per_capita":       "NY.GDP.PCAP.CD",
    "gini_index":           "SI.POV.GINI",
    "gov_effectiveness":    "GE.EST",
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

CACHE_TTL = timedelta(hours=24)
_live_cache: Dict[str, Dict] = {}
_cache_timestamps: Dict[str, datetime] = {}
_refresh_status = {"last_refresh": None, "countries_refreshed": 0, "status": "idle"}


def _is_cache_valid(country: str) -> bool:
    ts = _cache_timestamps.get(country)
    if ts is None:
        return False
    return datetime.now() - ts < CACHE_TTL


def invalidate_cache(country: Optional[str] = None):
    if country:
        _live_cache.pop(country, None)
        _cache_timestamps.pop(country, None)
    else:
        _live_cache.clear()
        _cache_timestamps.clear()


def _fetch_indicator(iso3: str, indicator: str, mrv: int = 3) -> Optional[float]:
    url = f"{WB_BASE}/country/{iso3}/indicator/{indicator}?format=json&per_page={mrv}&mrv={mrv}"
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


OECD_INDICATORS = {
    "gdp_per_capita": "SNA_TABLE1/GDP_PER_CAPITA",
    "gini_index":     "IDD/GINI",
}

def _fetch_oecd(iso3: str, key: str) -> Optional[float]:
    path = OECD_INDICATORS.get(key)
    if not path:
        return None
    url = f"https://stats.oecd.org/restsdmx/sdmx.ashx/GetData/{path}/{iso3}/all?startTime=2018&endTime=2025"
    try:
        resp = httpx.get(url, timeout=10, headers={"Accept": "application/json"})
        if resp.status_code == 200:
            data = resp.json()
            obs = data.get("dataSets", [{}])[0].get("observations", {})
            if obs:
                last_key = sorted(obs.keys())[-1]
                return round(float(obs[last_key][0]), 2)
    except Exception as e:
        logger.debug(f"OECD fallback error for {iso3}/{key}: {e}")
    return None


def _fetch_with_fallback(iso3: str, key: str, indicator_code: str,
                         country_name: str) -> Optional[float]:
    val = _fetch_indicator(iso3, indicator_code)
    if val is not None:
        return val
    val = _fetch_oecd(iso3, key)
    if val is not None:
        return val
    bench = BENCHMARKS.get(country_name, {})
    if key in bench:
        logger.info(f"Using static benchmark for {country_name}/{key}")
        return bench[key]
    return None


def get_country_esg_realtime(country_name: str) -> Optional[Dict]:
    if _is_cache_valid(country_name):
        return _live_cache[country_name]

    iso3 = COUNTRY_ISO3.get(country_name)
    if not iso3:
        return None

    result = {"country": country_name, "iso3": iso3, "source": "World Bank API"}

    for key, indicator_code in INDICATORS.items():
        val = _fetch_with_fallback(iso3, key, indicator_code, country_name)
        if val is not None:
            result[key] = val

    if len(result) <= 3:
        return None

    _live_cache[country_name] = result
    _cache_timestamps[country_name] = datetime.now()
    return result


def get_country_context(country_name: str) -> Optional[Dict]:
    return get_country_esg_realtime(country_name)


def refresh_all_countries() -> Dict:
    results = {}
    for name in COUNTRY_ISO3:
        invalidate_cache(name)
        data = get_country_esg_realtime(name)
        if data:
            results[name] = data
    return {"fetched": len(results), "total": len(COUNTRY_ISO3), "countries": results}


def refresh_live_data() -> Dict:
    _refresh_status["status"] = "running"
    result = refresh_all_countries()
    _refresh_status["last_refresh"] = datetime.now().isoformat()
    _refresh_status["countries_refreshed"] = result["fetched"]
    _refresh_status["status"] = "idle"
    return result


def get_refresh_status() -> Dict:
    all_countries = get_supported_countries()
    expired = sum(1 for c in _live_cache if not _is_cache_valid(c))
    return {
        "static_countries": len(all_countries),
        "live_cached": len(_live_cache),
        "cache_expired": expired,
        "cache_ttl_hours": CACHE_TTL.total_seconds() / 3600,
        "running": _refresh_status["status"] == "running",
        **_refresh_status,
    }


def get_supported_countries() -> List[str]:
    all_names = set(COUNTRY_ISO3.keys()) | set(BENCHMARKS.keys())
    return sorted(all_names)


def get_merged_country_data(name: str) -> Optional[Dict]:
    bench = BENCHMARKS.get(name)
    live = _live_cache.get(name) if _is_cache_valid(name) else None
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
