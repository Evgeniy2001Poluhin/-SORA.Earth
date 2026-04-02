"""
External ESG data provider.
Downloads and caches real-world country-level indicators
from the World Bank API and UNDP HDI datasets.
"""

import json
import os
import time
import logging
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger("sora_earth")

CACHE_DIR = Path("data/external")
CACHE_TTL = 86400  # 24 hours

# Built-in comprehensive dataset (used as fallback and base)
COUNTRY_ESG_DATA: Dict[str, Dict[str, Any]] = {
    "Sweden":       {"co2_per_capita": 3.5, "renewable_share": 60.1, "esg_rank": 1,  "hdi": 0.947, "electricity_access": 100, "forest_cover_pct": 68.7, "gdp_per_capita": 55689, "population_millions": 10.4},
    "Norway":       {"co2_per_capita": 7.5, "renewable_share": 71.6, "esg_rank": 3,  "hdi": 0.961, "electricity_access": 100, "forest_cover_pct": 33.2, "gdp_per_capita": 82832, "population_millions": 5.4},
    "Switzerland":  {"co2_per_capita": 4.0, "renewable_share": 74.5, "esg_rank": 5,  "hdi": 0.962, "electricity_access": 100, "forest_cover_pct": 31.7, "gdp_per_capita": 93457, "population_millions": 8.7},
    "Netherlands":  {"co2_per_capita": 8.1, "renewable_share": 13.2, "esg_rank": 7,  "hdi": 0.941, "electricity_access": 100, "forest_cover_pct": 11.2, "gdp_per_capita": 57768, "population_millions": 17.5},
    "Germany":      {"co2_per_capita": 7.9, "renewable_share": 46.3, "esg_rank": 8,  "hdi": 0.942, "electricity_access": 100, "forest_cover_pct": 32.7, "gdp_per_capita": 51204, "population_millions": 83.2},
    "UK":           {"co2_per_capita": 5.2, "renewable_share": 43.1, "esg_rank": 10, "hdi": 0.929, "electricity_access": 100, "forest_cover_pct": 13.1, "gdp_per_capita": 46510, "population_millions": 67.3},
    "France":       {"co2_per_capita": 4.5, "renewable_share": 44.2, "esg_rank": 12, "hdi": 0.903, "electricity_access": 100, "forest_cover_pct": 31.4, "gdp_per_capita": 43659, "population_millions": 67.8},
    "Italy":        {"co2_per_capita": 5.3, "renewable_share": 41.0, "esg_rank": 14, "hdi": 0.895, "electricity_access": 100, "forest_cover_pct": 32.0, "gdp_per_capita": 35657, "population_millions": 59.1},
    "Canada":       {"co2_per_capita": 15.5, "renewable_share": 67.8, "esg_rank": 15, "hdi": 0.936, "electricity_access": 100, "forest_cover_pct": 38.7, "gdp_per_capita": 52722, "population_millions": 38.2},
    "Spain":        {"co2_per_capita": 5.0, "renewable_share": 47.3, "esg_rank": 16, "hdi": 0.905, "electricity_access": 100, "forest_cover_pct": 36.8, "gdp_per_capita": 30116, "population_millions": 47.4},
    "Japan":        {"co2_per_capita": 8.5, "renewable_share": 20.3, "esg_rank": 18, "hdi": 0.925, "electricity_access": 100, "forest_cover_pct": 68.4, "gdp_per_capita": 39313, "population_millions": 125.7},
    "Australia":    {"co2_per_capita": 15.0, "renewable_share": 32.5, "esg_rank": 25, "hdi": 0.951, "electricity_access": 100, "forest_cover_pct": 17.4, "gdp_per_capita": 64491, "population_millions": 25.7},
    "South Korea":  {"co2_per_capita": 11.6, "renewable_share": 8.1, "esg_rank": 28, "hdi": 0.925, "electricity_access": 100, "forest_cover_pct": 63.4, "gdp_per_capita": 34998, "population_millions": 51.7},
    "USA":          {"co2_per_capita": 14.7, "renewable_share": 21.5, "esg_rank": 35, "hdi": 0.921, "electricity_access": 100, "forest_cover_pct": 33.9, "gdp_per_capita": 76343, "population_millions": 331.9},
    "Brazil":       {"co2_per_capita": 2.2, "renewable_share": 83.2, "esg_rank": 42, "hdi": 0.754, "electricity_access": 99.8, "forest_cover_pct": 59.4, "gdp_per_capita": 8918, "population_millions": 214.3},
    "China":        {"co2_per_capita": 7.6, "renewable_share": 29.4, "esg_rank": 47, "hdi": 0.768, "electricity_access": 100, "forest_cover_pct": 23.3, "gdp_per_capita": 12556, "population_millions": 1412.0},
    "India":        {"co2_per_capita": 1.9, "renewable_share": 38.7, "esg_rank": 52, "hdi": 0.633, "electricity_access": 99.0, "forest_cover_pct": 24.3, "gdp_per_capita": 2389, "population_millions": 1417.2},
    "Mexico":       {"co2_per_capita": 3.5, "renewable_share": 26.1, "esg_rank": 55, "hdi": 0.758, "electricity_access": 99.1, "forest_cover_pct": 33.9, "gdp_per_capita": 10946, "population_millions": 128.9},
    "Russia":       {"co2_per_capita": 11.4, "renewable_share": 19.7, "esg_rank": 58, "hdi": 0.822, "electricity_access": 100, "forest_cover_pct": 49.8, "gdp_per_capita": 12195, "population_millions": 144.1},
    "South Africa": {"co2_per_capita": 7.5, "renewable_share": 11.3, "esg_rank": 60, "hdi": 0.713, "electricity_access": 84.4, "forest_cover_pct": 7.6,  "gdp_per_capita": 6994, "population_millions": 59.4},
    "Indonesia":    {"co2_per_capita": 2.3, "renewable_share": 15.5, "esg_rank": 53, "hdi": 0.705, "electricity_access": 97.0, "forest_cover_pct": 49.1, "gdp_per_capita": 4332, "population_millions": 273.5},
    "Turkey":       {"co2_per_capita": 5.1, "renewable_share": 42.4, "esg_rank": 49, "hdi": 0.838, "electricity_access": 100, "forest_cover_pct": 28.6, "gdp_per_capita": 9327, "population_millions": 84.3},
    "Saudi Arabia": {"co2_per_capita": 16.6, "renewable_share": 0.3,  "esg_rank": 68, "hdi": 0.875, "electricity_access": 100, "forest_cover_pct": 0.5,  "gdp_per_capita": 23586, "population_millions": 36.0},
    "UAE":          {"co2_per_capita": 20.7, "renewable_share": 7.2,  "esg_rank": 62, "hdi": 0.911, "electricity_access": 100, "forest_cover_pct": 4.5,  "gdp_per_capita": 44316, "population_millions": 9.4},
    "Nigeria":      {"co2_per_capita": 0.6, "renewable_share": 18.2, "esg_rank": 72, "hdi": 0.535, "electricity_access": 55.4, "forest_cover_pct": 7.2,  "gdp_per_capita": 2184, "population_millions": 218.5},
    "Kenya":        {"co2_per_capita": 0.4, "renewable_share": 80.1, "esg_rank": 48, "hdi": 0.575, "electricity_access": 71.4, "forest_cover_pct": 7.8,  "gdp_per_capita": 2099, "population_millions": 54.0},
    "Egypt":        {"co2_per_capita": 2.4, "renewable_share": 12.0, "esg_rank": 64, "hdi": 0.731, "electricity_access": 100, "forest_cover_pct": 0.1,  "gdp_per_capita": 3699, "population_millions": 104.3},
    "Argentina":    {"co2_per_capita": 3.9, "renewable_share": 31.2, "esg_rank": 50, "hdi": 0.842, "electricity_access": 99.0, "forest_cover_pct": 10.7, "gdp_per_capita": 10636, "population_millions": 45.8},
    "Chile":        {"co2_per_capita": 4.3, "renewable_share": 52.1, "esg_rank": 30, "hdi": 0.855, "electricity_access": 100, "forest_cover_pct": 24.5, "gdp_per_capita": 16265, "population_millions": 19.5},
    "Poland":       {"co2_per_capita": 7.8, "renewable_share": 22.1, "esg_rank": 38, "hdi": 0.876, "electricity_access": 100, "forest_cover_pct": 30.8, "gdp_per_capita": 17841, "population_millions": 37.7},
}


def _compute_global_average() -> Dict[str, float]:
    """Compute global average across all countries in dataset."""
    keys = ["co2_per_capita", "renewable_share", "esg_rank", "hdi",
            "electricity_access", "forest_cover_pct", "gdp_per_capita"]
    n = len(COUNTRY_ESG_DATA)
    avg = {}
    for k in keys:
        avg[k] = round(sum(c.get(k, 0) for c in COUNTRY_ESG_DATA.values()) / n, 2)
    return avg


def get_country_context(country: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Return external ESG context for a given country.
    Includes country indicators, global averages, and comparison deltas.
    """
    if not country:
        return None

    # Normalize country name
    country_normalized = country.strip().title()

    data = COUNTRY_ESG_DATA.get(country_normalized)
    if not data:
        return None

    global_avg = _compute_global_average()

    comparison = {}
    for key in ["co2_per_capita", "renewable_share", "esg_rank", "hdi",
                "electricity_access", "forest_cover_pct", "gdp_per_capita"]:
        if key in data and key in global_avg:
            comparison[key] = round(data[key] - global_avg[key], 2)

    # Risk flags
    risk_flags = []
    if data.get("co2_per_capita", 0) > 10:
        risk_flags.append("High CO₂ emissions per capita — regulatory risk")
    if data.get("renewable_share", 0) < 15:
        risk_flags.append("Low renewable energy shartransition risk")
    if data.get("hdi", 0) < 0.7:
        risk_flags.append("Low HDI — social and governance challenges")
    if data.get("electricity_access", 100) < 90:
        risk_flags.append("Limited electricity access — infrastructure risk")
    if data.get("forest_cover_pct", 0) < 10:
        risk_flags.append("Low forest cover — biodiversity and carbon sink risk")

    # ESG tier
    esg_rank = data.get("esg_rank", 50)
    if esg_rank <= 10:
        tier = "Leader"
    elif esg_rank <= 25:
        tier = "Strong"
    elif esg_rank <= 45:
        tier = "Average"
    else:
        tier = "Below Average"

    return {
        "country": country_normalized,
        "esg_tier": tier,
        "indicators": data,
        "global_average": global_avg,
        "comparison_vs_global": comparison,
        "risk_flags": risk_flags,
        "data_sources": [
            "World Bank Open Data (CO₂, renewable energy, electricity access)",
            "UNDP Human Development Index",
            "Yale EPInmental Performance Index)",
            "Global Forest Watch"
        ],
        "last_updated": "2025-12-01"
    }


def get_supported_countries():
    """Return list of all supported countries."""
    return sorted(COUNTRY_ESG_DATA.keys())


# ===== LIVE DATA FROM WORLD BANK API =====
import requests
from datetime import datetime as _dt

WB_INDICATORS = {
    "co2_kt": "EN.ATM.CO2E.KT",
    "renewable_share": "EG.FEC.RNEW.ZS",
    "gdp_per_capita": "NY.GDP.PCAP.CD",
    "forest_cover_pct": "AG.LND.FRST.ZS",
    "population": "SP.POP.TOTL",
    "internet_users_pct": "IT.NET.USER.ZS",
}

COUNTRY_ISO = {
    "Sweden": "SWE", "Norway": "NOR", "Switzerland": "CHE", "Netherlands": "NLD",
    "Germany": "DEU", "UK": "GBR", "France": "FRA", "Italy": "ITA", "Canada": "CAN",
    "Spain": "ESP", "Japan": "JPN", "Australia": "AUS", "South Korea": "KOR",
    "USA": "USA", "Brazil": "BRA", "China": "CHN", "India": "IND", "Mexico": "MEX",
    "Russia": "RUS", "South Africa": "ZAF", "Indonesia": "IDN", "Turkey": "TUR",
    "Saudi Arabia": "SAU", "UAE": "ARE", "Nigeria": "NGA", "Kenya": "KEN",
    "Egypt": "EGY", "Argentina": "ARG", "Chile": "CHL", "Poland": "POL",
}

_live_cache: Dict[str, Any] = {}
_last_refresh: Optional[str] = None
_refresh_status: str = "never"
_refresh_errors: list = []


def _fetch_wb_indicator(iso: str, indicator: str) -> Optional[float]:
    """Fetch latest value from World Bank API via curl subprocess."""
    import subprocess
    url = f"https://api.worldbank.org/v2/country/{iso}/indicator/{indicator}?format=json&date=2018:2024&per_page=10"
    try:
        r = subprocess.run(
            ["curl", "-s", "-m", "15", url],
            capture_output=True, text=True, timeout=20
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None
        data = json.loads(r.stdout)
        if len(data) < 2 or not data[1]:
            return None
        for entry in data[1]:
            if entry.get("value") is not None:
                return round(float(entry["value"]), 2)
        return None
    except Exception:
        return None

def refresh_live_data() -> Dict[str, Any]:
    """Fetch fresh data from World Bank for all countries. Returns status report."""
    global _live_cache, _last_refresh, _refresh_status, _refresh_errors
    _refresh_errors = []
    updated = 0
    failed = 0

    for country, iso in COUNTRY_ISO.items():
        country_data = {}
        for field, wb_code in WB_INDICATORS.items():
            val = _fetch_wb_indicator(iso, wb_code)
            if val is not None:
                country_data[field] = val

        # Derive co2_per_capita from co2_kt and population
        if "co2_kt" in country_data and "population" in country_data and country_data["population"] > 0:
            country_data["co2_per_capita"] = round(country_data["co2_kt"] * 1000 / country_data["population"], 2)
        if "population" in country_data:
            country_data["population_millions"] = round(country_data["population"] / 1_000_000, 1)

        if country_data:
            # Merge with static: live overwrites static
            base = COUNTRY_ESG_DATA.get(country, {}).copy()
            base.update(country_data)
            _live_cache[country] = base
            updated += 1
        else:
            _refresh_errors.append(country)
            failed += 1

    # Save to disk cache
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / "wb_live.json"
    cache_file.write_text(json.dumps({
        "data": _live_cache,
        "timestamp": _dt.utcnow().isoformat(),
    }, indent=2))

    _last_refresh = _dt.utcnow().isoformat()
    _refresh_status = "ok" if failed == 0 else "partial"

    return {
        "status": _refresh_status,
        "updated": updated,
        "failed": failed,
        "failed_countries": _refresh_errors,
        "timestamp": _last_refresh,
    }


def get_merged_country_data(country: str) -> Optional[Dict[str, Any]]:
    """Get country data: live (if available) > static fallback."""
    _load_disk_cache()
    name = country.strip().title()
    if name in _live_cache:
        return _live_cache[name]
    return COUNTRY_ESG_DATA.get(name)


def get_all_countries_merged() -> Dict[str, Dict]:
    """All countries with live data merged over static."""
    _load_disk_cache()
    result = {}
    for country in set(list(COUNTRY_ESG_DATA.keys()) + list(_live_cache.keys())):
        base = COUNTRY_ESG_DATA.get(country, {}).copy()
        base.update(_live_cache.get(country, {}))
        result[country] = base
    return result


def _load_disk_cache():
    """Load cached live data from disk if memory cache is empty."""
    global _live_cache, _last_refresh
    if _live_cache:
        return
    cache_file = CACHE_DIR / "wb_live.json"
    if cache_file.exists():
        try:
            raw = json.loads(cache_file.read_text())
            _live_cache = raw.get("data", {})
            _last_refresh = raw.get("timestamp")
        except Exception:
            pass


def get_refresh_status() -> Dict[str, Any]:
    return {
        "last_refresh": _last_refresh,
        "status": _refresh_status,
        "live_countries": len(_live_cache),
        "static_countries": len(COUNTRY_ESG_DATA),
        "errors": _refresh_errors,
    }
