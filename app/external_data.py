"""Real ESG data from World Bank API + OECD fallback + static benchmarks with TTL cache.

Fixes applied (09.04.2026):
- Removed duplicate _fetch_indicator definition
- Separated _fetch_wb_indicator and _fetch_oecd_indicator
- Fixed _fetch_with_fallback (was using undefined 'key' variable)
- Added missing csv/io imports
- Added source tracking per indicator
- Added CountryIndicatorHistory persistence in refresh_all_countries
"""
import csv
import io
import logging
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple

import httpx

from app.country_benchmarks import BENCHMARKS, GLOBAL_AVG
from app.database import SessionLocal, DataRefreshLog, CountryIndicatorHistory


logger = logging.getLogger("sora")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WB_BASE = "https://api.worldbank.org/v2"

INDICATORS: Dict[str, str] = {
    "co2_per_capita":    "EN.ATM.CO2E.PC",
    "renewable_share":   "EG.FEC.RNEW.ZS",
    "life_expectancy":   "SP.DYN.LE00.IN",
    "gdp_per_capita":    "NY.GDP.PCAP.CD",
    "gini_index":        "SI.POV.GINI",
    "gov_effectiveness": "GE.EST",
}

COUNTRY_ISO3: Dict[str, str] = {
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

# OECD SDMX flows (only for indicators that have OECD equivalents)
OECD_FLOWS: Dict[str, str] = {
    "gdp_per_capita": (
        "OECD.SDD.NAD,DSD_NAAG@DF_NAAG_I/"
        "A.{iso3}.S1.GDP_CAP.V_USD.G"
    ),
    "gini_index": (
        "OECD.WISE.INE,DSD_WISE_IDD@DF_IDD/"
        "A.{iso3}.GINI.TOT_POP.D_MDEF.CURRENT"
    ),
}

CACHE_TTL = timedelta(hours=24)

# In-memory cache (per-process)
_live_cache: Dict[str, Dict] = {}
_cache_timestamps: Dict[str, datetime] = {}

# Refresh status (per-process, TODO: move to DB/Redis for multi-worker)
_refresh_status: Dict = {
    "last_refresh": None,
    "countries_refreshed": 0,
    "status": "idle",
}

# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _is_cache_valid(country: str) -> bool:
    ts = _cache_timestamps.get(country)
    if ts is None:
        return False
    return datetime.now() - ts < CACHE_TTL


def invalidate_cache(country: Optional[str] = None) -> None:
    """Invalidate live cache for one country or all."""
    if country:
        _live_cache.pop(country, None)
        _cache_timestamps.pop(country, None)
    else:
        _live_cache.clear()
        _cache_timestamps.clear()

# ---------------------------------------------------------------------------
# Data fetchers — each is a single, clean function
# ---------------------------------------------------------------------------

def _fetch_wb_indicator(iso3: str, indicator_code: str, mrv: int = 3) -> Optional[float]:
    """Fetch latest non-null value from World Bank API.

    Returns float or None on any error / missing data.
    """
    if os.getenv("SORA_OFFLINE", "0") == "1":
        return None

    url = (
        f"{WB_BASE}/country/{iso3}/indicator/{indicator_code}"
        f"?format=json&per_page={mrv}&mrv={mrv}"
    )
    try:
        resp = httpx.get(url, timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        if len(data) >= 2 and data[1]:
            for entry in data[1]:
                if entry.get("value") is not None:
                    return round(float(entry["value"]), 2)
    except Exception as e:
        logger.warning("World Bank API error for %s/%s: %s", iso3, indicator_code, e)
    return None


def _fetch_oecd_indicator(iso3: str, key: str) -> Optional[float]:
    """Fetch indicator from OECD SDMX (CSV format).

    Only works for keys present in OECD_FLOWS.
    Returns float or None.
    """
    if os.getenv("SORA_OFFLINE", "0") == "1":
        return None

    flow_template = OECD_FLOWS.get(key)
    if not flow_template:
        return None

    flow = flow_template.replace("{iso3}", iso3)
    url = (
        f"https://sdmx.oecd.org/public/rest/data/{flow}"
        "?startPeriod=2018&dimensionAtObservation=AllDimensions&format=csvfile"
    )
    try:
        resp = httpx.get(url, timeout=5.0, headers={"Accept": "text/csv"})
        resp.raise_for_status()
        text = resp.text.strip()
        if not text:
            return None
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        if not rows:
            return None
        val = rows[-1].get("OBS_VALUE")
        if not val:
            return None
        return round(float(val), 2)
    except Exception as e:
        logger.debug("OECD fallback error for %s/%s: %s", iso3, key, e)
    return None


def _fetch_with_fallback_impl(
    iso3: str,
    key: str,
    indicator_code: str,
    country_name: str,
) -> Tuple[Optional[float], str]:
    """Unified fetch with fallback chain.

    Order: World Bank → OECD → static benchmark → global average.
    Returns (value, source_tag).
    """
    # 1. World Bank
    val = _fetch_wb_indicator(iso3, indicator_code)
    if val is not None:
        return val, "world_bank"

    # 2. OECD (only for keys that have a mapping)
    val = _fetch_oecd_indicator(iso3, key)
    if val is not None:
        return val, "oecd"

    # 3. Static benchmark
    bench = BENCHMARKS.get(country_name, {})
    if key in bench:
        logger.info("Using static benchmark for %s/%s", country_name, key)
        return bench[key], "benchmark"

    # 4. Global average (offline mode only)
    if os.getenv("SORA_OFFLINE", "0") == "1" and key in GLOBAL_AVG:
        return GLOBAL_AVG[key], "global_avg"

    return None, "none"

# ---------------------------------------------------------------------------
# Public API — country data
# ---------------------------------------------------------------------------

def get_country_esg_realtime(country_name: str) -> Optional[Dict]:
    """Get ESG profile for a country (cached, with fallback chain)."""
    if _is_cache_valid(country_name):
        return _live_cache[country_name]

    iso3 = COUNTRY_ISO3.get(country_name)
    if not iso3:
        return None

    result: Dict = {
        "country": country_name,
        "iso3": iso3,
        "source": "World Bank API + OECD + benchmarks",
        "indicators": {},
        "indicator_sources": {},
    }

    for key, indicator_code in INDICATORS.items():
        val, src = _fetch_with_fallback_impl(iso3, key, indicator_code, country_name)
        if val is not None:
            result["indicators"][key] = val
            result["indicator_sources"][key] = src
            # backward compat: top-level keys too
            result[key] = val

    if not result["indicators"]:
        return None

    _live_cache[country_name] = result
    _cache_timestamps[country_name] = datetime.now()
    return result


def get_country_context(country_name: str) -> Optional[Dict]:
    """Alias for backward compatibility."""
    return get_country_esg_realtime(country_name)


def get_supported_countries() -> List[str]:
    """All country names available (ISO3 map + benchmarks)."""
    return sorted(set(COUNTRY_ISO3.keys()) | set(BENCHMARKS.keys()))


def get_merged_country_data(name: str) -> Optional[Dict]:
    """Merge static benchmarks + live cache for a single country."""
    bench = BENCHMARKS.get(name)
    live = _live_cache.get(name) if _is_cache_valid(name) else None
    if not bench and not live:
        return None
    result: Dict = {}
    if bench:
        result.update(bench)
    if live:
        result["live"] = live
    return result


def get_all_countries_merged() -> Dict[str, dict]:
    """All countries with merged live + static data."""
    result: Dict[str, dict] = {}
    for name in get_supported_countries():
        merged = get_merged_country_data(name)
        result[name] = merged if merged else {"source": "name_only"}
    return result

# ---------------------------------------------------------------------------
# Refresh logic
# ---------------------------------------------------------------------------

def refresh_all_countries() -> Dict:
    """Full refresh: invalidate cache, fetch live data, persist history."""
    db = SessionLocal()
    results: Dict[str, dict] = {}
    try:
        fetched_at = datetime.utcnow()
        for name, iso3 in COUNTRY_ISO3.items():
            invalidate_cache(name)
            data = get_country_esg_realtime(name)
            if not data:
                continue
            results[name] = data

            # Persist indicator history (versioning)
            for key, value in data.get("indicators", {}).items():
                hist = CountryIndicatorHistory(
                    country_iso3=iso3,
                    country_name=name,
                    indicator_code=INDICATORS.get(key, key),
                    indicator_name=key,
                    value=value,
                    source=data.get("indicator_sources", {}).get(key, "unknown"),
                    as_of_date=None,
                    fetched_at=fetched_at,
                    refresh_job_name="external_data_refresh",
                )
                db.add(hist)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception("Error during refresh_all_countries: %s", e)
        raise
    finally:
        db.close()

    return {"fetched": len(results), "total": len(COUNTRY_ISO3), "countries": results}


def refresh_live_data(trigger_source: str = "manual") -> Dict:
    """Background refresh: fetch all countries + write DataRefreshLog."""
    db = SessionLocal()
    _start = datetime.utcnow()
    log = DataRefreshLog(
        status="running",
        countries_fetched=0,
        total_countries=len(COUNTRY_ISO3),
        message=None,
        job_name="external_data_refresh",
        source="world_bank_oecd",
        started_at=_start,
        trigger_source=trigger_source,
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    try:
        _refresh_status["status"] = "running"
        result = refresh_all_countries()

        _refresh_status["last_refresh"] = datetime.utcnow().isoformat()
        _refresh_status["countries_refreshed"] = result["fetched"]
        _refresh_status["status"] = "idle"

        _end = datetime.utcnow()
        log.status = "success"
        log.countries_fetched = result["fetched"]
        log.total_countries = result["total"]
        log.message = "OK"
        log.fetched_at = _end
        log.finished_at = _end
        log.duration_sec = round((_end - _start).total_seconds(), 2)
        db.commit()
        return result

    except Exception as e:
        _refresh_status["status"] = "idle"
        _end = datetime.utcnow()
        log.status = "error"
        log.message = str(e)[:500]
        log.finished_at = _end
        log.duration_sec = round((_end - _start).total_seconds(), 2)
        db.commit()
        logger.exception("refresh_live_data failed: %s", e)
        raise
    finally:
        db.close()


def get_refresh_status() -> Dict:
    """Current cache and refresh status — persisted via DataRefreshLog."""
    all_countries = get_supported_countries()
    expired = sum(1 for c in _live_cache if not _is_cache_valid(c))

    # Read last successful refresh from DB
    db = SessionLocal()
    try:
        last = (
            db.query(DataRefreshLog)
            .filter(DataRefreshLog.status == "success")
          .order_by(DataRefreshLog.timestamp.desc())
            .first()
        )
        last_refresh = last.timestamp.isoformat() if last and last.timestamp else _refresh_status.get("last_refresh")
        countries_refreshed = last.countries_fetched if last else _refresh_status.get("countries_refreshed", 0)
    finally:
        db.close()

    return {
        "static_countries": len(all_countries),
        "live_cached": len(_live_cache),
        "cache_expired": expired,
        "cache_ttl_hours": CACHE_TTL.total_seconds() / 3600,
        "running": _refresh_status["status"] == "running",
        "status": _refresh_status["status"],
        "last_refresh": last_refresh,
        "countries_refreshed": countries_refreshed,
    }

# --- Backward-compat shims for tests and older code ---

def _fetch_indicator(iso3: str, indicator: str, mrv: int = 3):
    """Backward-compatible alias for tests (World Bank)."""
    return _fetch_wb_indicator(iso3, indicator, mrv)


def _fetch_oecd(iso3: str, key: str):
    """Backward-compatible alias for tests (OECD)."""
    return _fetch_oecd_indicator(iso3, key)

# --- Backward-compat for tests expecting _fetch_with_fallback to return just value ---


def _fetch_with_fallback(*args, **kwargs):
    """Backward-compatible wrapper: keep only value for old tests."""
    val, _src = _fetch_with_fallback_legacy_ref(*args, **kwargs)
    return val

# --- Backward-compat wrapper for tests importing _fetch_with_fallback ---

def _fetch_with_fallback(
    iso3: str,
    key: str,
    indicator_code: str,
    country_name: str,
    *,
    want_source: bool = False,
):
    """
    Backward-compatible wrapper.
    - Старые тесты зовут без want_source -> получают только value.
    - Новый код может звать с want_source=True -> (value, source).
    """
    val, src = _fetch_with_fallback_impl(iso3, key, indicator_code, country_name)
    if want_source:
        return val, src
    return val

def _fetch_with_fallback(
    iso3: str,
    key: str,
    indicator_code: str,
    country_name: str,
    *,
    want_source: bool = False,
):
    val, src = _fetch_with_fallback_impl(iso3, key, indicator_code, country_name)
    if want_source:
        return val, src
    return val
