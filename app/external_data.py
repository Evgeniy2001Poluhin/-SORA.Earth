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
import time
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

# Retry budget for the series fetch. Deliberately small: this runs 210 times in
# a row, so a generous budget turns one bad afternoon at the source into a
# refresh that never finishes.
_WB_RETRIES = 3
_WB_BACKOFF = 1.0  # seconds, doubled per attempt


def history_refresh_enabled() -> bool:
    """Whether the scheduled refresh may write indicator history.

    Off by default, and that is the point. `auto_refresh_external_data` is in
    the scheduler's immediate-run set, so deploying the series write path with
    it enabled would start a first mass ingestion during the deployment itself
    -- unobserved, and racing anyone trying to run it by hand.

    The sequence this exists to allow: deploy with it off, run
    scripts/refresh_indicator_history.py once with the counters in view, check
    the database, then turn it on.

    Read at call time so the flag can be flipped by restarting one container
    rather than rebuilding.
    """
    return os.getenv("SORA_HISTORY_REFRESH", "off").strip().lower() in {
        "1", "true", "on", "yes",
    }

INDICATORS: Dict[str, str] = {
    "co2_per_capita":    "EN.ATM.CO2E.PC",
    "renewable_share":   "EG.FEC.RNEW.ZS",
    "life_expectancy":   "SP.DYN.LE00.IN",
    "gdp_per_capita":    "NY.GDP.PCAP.CD",
    # Consumed by the gdp_growth forecasting regressor, which asked for this
    # code while nothing collected it -- so the column was 0.0 in every
    # forecast ever made (#86). NY.GDP.PCAP.CD above is a level in dollars,
    # not a rate, and cannot stand in for it.
    "gdp_growth":        "NY.GDP.MKTP.KD.ZG",
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


def _fetch_wb_indicator_dated(
    iso3: str, indicator_code: str, mrv: int = 3
) -> Tuple[Optional[float], Optional[str]]:
    """The same fetch, keeping the period the value describes.

    `_fetch_wb_indicator` returns the number and discards `entry["date"]`, which
    is the year the World Bank states the observation is for. Every row in
    country_indicator_history was stored with `as_of_date=None` because of it --
    87,005 of them -- so the database could say when a figure was downloaded and
    never what it measured. See issue #58.

    Returns (value, period). The period is the source's own string, e.g. "2025",
    kept verbatim rather than parsed into a date here: an annual indicator has a
    year, not a day, and inventing 1 January would assert precision the source
    did not give.
    """
    if os.getenv("SORA_OFFLINE", "0") == "1":
        return None, None

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
                    return round(float(entry["value"]), 2), entry.get("date")
    except Exception as e:
        logger.warning("World Bank API error for %s/%s: %s", iso3, indicator_code, e)
    return None, None


def _fetch_wb_series(
    iso3: str, indicator_code: str, per_page: int = 500
) -> List[Tuple[Optional[str], Optional[float]]]:
    """Every annual observation the World Bank publishes, newest first.

    `_fetch_wb_indicator_dated` asks for three (`mrv=3`) and returns the first
    non-null, discarding the rest -- so the refresh could only ever store one
    point per country and did, ~450 times over (#86, #95). The API serves the
    whole series without `mrv`; for these indicators that is 66 annual rows.

    Rows are returned as the source gave them, including nulls and missing
    dates. Deciding what is storable belongs to the caller, which counts what
    it refused; discarding here would make a padded year and a rejected one
    indistinguishable.

    Pagination is followed rather than assumed away: `per_page=500` covers
    every indicator in INDICATORS today, and a series that outgrows it would
    otherwise be silently truncated at exactly the point nobody is looking.
    """
    if os.getenv("SORA_OFFLINE", "0") == "1":
        return []

    out: List[Tuple[Optional[str], Optional[float]]] = []
    page = 1
    while True:
        url = (
            f"{WB_BASE}/country/{iso3}/indicator/{indicator_code}"
            f"?format=json&per_page={per_page}&page={page}"
        )
        # A full-series refresh is ~210 sequential requests where the old one
        # was ~210 cheap ones, so a single 429 or a transient 5xx used to cost
        # one stale value and now costs a whole country's history. Retried with
        # backoff; anything else (4xx that is not 429, malformed payload) is
        # not worth retrying and returns what was gathered.
        data = None
        for attempt in range(_WB_RETRIES):
            retryable = None
            try:
                resp = httpx.get(url, timeout=10.0)
                if resp.status_code == 429 or resp.status_code >= 500:
                    retryable = f"status {resp.status_code}"
                else:
                    resp.raise_for_status()
                    data = resp.json()
                    break
            # Timeouts and transport errors are retried too. Measured: a real
            # full-series request takes 0.3-1.1s against a 10s budget, so a
            # timeout here is transient -- and the first version treated it as
            # fatal, giving up on a country's whole history for one slow
            # connection. Across 210 sequential requests these are likelier
            # than 429.
            except (httpx.TimeoutException, httpx.TransportError) as e:
                retryable = type(e).__name__
            except Exception as e:
                logger.warning("World Bank series error for %s/%s page %d: %s",
                               iso3, indicator_code, page, e)
                return out

            if retryable is not None:
                if attempt == _WB_RETRIES - 1:
                    logger.warning(
                        "World Bank series gave up for %s/%s page %d after "
                        "%d attempts (%s)",
                        iso3, indicator_code, page, _WB_RETRIES, retryable)
                    return out
                time.sleep(_WB_BACKOFF * (2 ** attempt))

        if data is None:
            return out

        if not isinstance(data, list) or len(data) < 2 or not data[1]:
            return out

        header, rows = data[0], data[1]
        for entry in rows:
            if not isinstance(entry, dict):
                continue
            value = entry.get("value")
            out.append((
                entry.get("date"),
                None if value is None else round(float(value), 2),
            ))

        pages = header.get("pages") if isinstance(header, dict) else 1
        if not isinstance(pages, int) or page >= pages:
            return out
        page += 1


def refresh_indicator_history(
    db=None,
    countries: Optional[Dict[str, str]] = None,
    indicators: Optional[Dict[str, str]] = None,
    job_name: str = "external_data_refresh",
) -> Dict[str, int]:
    """Store the full World Bank series for each (country, indicator).

    Separate from `refresh_all_countries`, which exists to fill the live cache
    the API serves and legitimately wants one current value per indicator. The
    history is a different question -- what the source has published over time
    -- and conflating them is why the table holds one point per country.

    Append-only, and not by convention: the trigger from #90 refuses any UPDATE
    of `value` or `fetched_at`, so a revision can only be a new row. The
    point-in-time read then resolves which value was in force when.

    Returns counts that each mean one thing:

        fetched     observations the API returned
        inserted    periods stored for the first time
        unchanged   periods already stored with the same value
        revised     periods the source restated, stored as a further row
        no_value    years the source published as null -- expected padding
        no_period   observations carrying no year -- zero unless something is wrong

    The last two were one counter called `rejected`, which made them
    indistinguishable. They are not the same thing: the World Bank pads a
    series with null years (30 of 66 for RUS/NY.GDP.MKTP.KD.ZG, the pre-1990
    ones), and counting those as rejections makes "rejected = 0" an acceptance
    criterion that can never pass. An observation with no year is a real
    defect, and it now has a counter that stays at zero when nothing is wrong.

    A run that reports only success is how 408 empty OpenAQ runs passed for
    healthy (#56), so every observation lands in exactly one of these.
    """
    from app.database import CountryIndicatorHistory

    countries = COUNTRY_ISO3 if countries is None else countries
    indicators = INDICATORS if indicators is None else indicators

    owns_session = db is None
    if owns_session:
        db = SessionLocal()

    stats = {"fetched": 0, "inserted": 0, "unchanged": 0, "revised": 0,
             "no_value": 0, "no_period": 0}

    try:
        fetched_at = datetime.utcnow()
        for name, iso3 in countries.items():
            for key, code in indicators.items():
                series = _fetch_wb_series(iso3, code)
                stats["fetched"] += len(series)

                # What is on record now, per period: the most recently fetched
                # row wins, which is the same rule the point-in-time read uses.
                current: Dict[datetime, float] = {}
                for as_of, value in db.query(
                    CountryIndicatorHistory.as_of_date,
                    CountryIndicatorHistory.value,
                ).filter(
                    CountryIndicatorHistory.country_iso3 == iso3,
                    CountryIndicatorHistory.indicator_code == code,
                    CountryIndicatorHistory.as_of_date.isnot(None),
                ).order_by(
                    CountryIndicatorHistory.fetched_at.asc(),
                    CountryIndicatorHistory.id.asc(),
                ):
                    current[as_of] = value

                for period, value in series:
                    as_of = _period_to_date(period)
                    if as_of is None:
                        stats["no_period"] += 1
                        continue
                    if value is None:
                        stats["no_value"] += 1
                        continue

                    known = current.get(as_of)
                    if known is not None and abs(known - value) < 1e-9:
                        stats["unchanged"] += 1
                        continue

                    db.add(CountryIndicatorHistory(
                        country_iso3=iso3,
                        country_name=name,
                        indicator_code=code,
                        indicator_name=key,
                        value=value,
                        source="world_bank",
                        as_of_date=as_of,
                        fetched_at=fetched_at,
                        refresh_job_name=job_name,
                    ))
                    current[as_of] = value
                    stats["revised" if known is not None else "inserted"] += 1

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        if owns_session:
            db.close()

    logger.info(
        "indicator history: fetched=%d inserted=%d unchanged=%d revised=%d "
        "no_value=%d no_period=%d",
        stats["fetched"], stats["inserted"], stats["unchanged"],
        stats["revised"], stats["no_value"], stats["no_period"],
    )
    return stats


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
) -> Tuple[Optional[float], str, Optional[str]]:
    """Unified fetch with fallback chain.

    Order: World Bank → OECD → static benchmark → global average.
    Returns (value, source_tag, period).

    The period travels in the return rather than in a side table. An earlier
    version kept a module-level dict keyed by (iso3, indicator_code), written
    just before the return that produced it -- which is correct only while calls
    are sequential, and nothing guarantees that. Two concurrent fetches of the
    same pair could have one overwrite the other's period, and the wrong
    as_of_date would be persisted against a right-looking value. Sources other
    than World Bank state no period and return None.
    """
    # 1. World Bank
    val, period = _fetch_wb_indicator_dated(iso3, indicator_code)
    if val is not None:
        return val, "world_bank", period

    # 2. OECD (only for keys that have a mapping)
    val = _fetch_oecd_indicator(iso3, key)
    if val is not None:
        return val, "oecd", None

    # 3. Static benchmark
    bench = BENCHMARKS.get(country_name, {})
    if key in bench:
        logger.info("Using static benchmark for %s/%s", country_name, key)
        return bench[key], "benchmark", None

    # 4. Global average (offline mode only)
    if os.getenv("SORA_OFFLINE", "0") == "1" and key in GLOBAL_AVG:
        return GLOBAL_AVG[key], "global_avg", None

    return None, "none", None

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
        # What period each value describes, when the source states one. World
        # Bank does; the static benchmarks and the offline global averages do
        # not, and they get None rather than a guess. See issue #58.
        "indicator_periods": {},
    }

    for key, indicator_code in INDICATORS.items():
        val, src, period = _fetch_with_fallback_impl(iso3, key, indicator_code, country_name)
        if val is not None:
            result["indicators"][key] = val
            result["indicator_sources"][key] = src
            result["indicator_periods"][key] = period
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

def _period_to_date(period: Optional[str]) -> Optional[datetime]:
    """The source's period string as a date, or None when it stated none.

    World Bank annual indicators carry a year: "2025". Stored as the first day of
    that year, because the column is a DateTime and there is nowhere else to put
    it -- but the day is an artefact of the column type, not a claim by the
    source. A specialist reading 2025-01-01 should understand "the 2025 figure",
    which is why the period is also kept verbatim upstream.

    None stays None. A benchmark or a global average has no period, and inventing
    one would make a fallback indistinguishable from a dated observation --
    exactly the confusion issue #58 is about.
    """
    if not period:
        return None
    text = str(period).strip()
    # Exactly four digits, nothing else. Slicing the first four characters turned
    # "2025-invalid" and "20250" into 2025-01-01 -- a false observation date,
    # which is worse than none: it looks answerable and is wrong.
    if not (len(text) == 4 and text.isdigit()):
        logger.warning("Unparseable indicator period %r; storing no date", period)
        return None
    try:
        return datetime(int(text), 1, 1)
    except ValueError:
        logger.warning("Out-of-range indicator period %r; storing no date", period)
        return None


def refresh_all_countries() -> Dict:
    """Refresh the live cache: one current figure per country and indicator.

    It used to persist history here as well, writing whatever the fallback
    chain currently resolved to on every run. That is how the table came to
    hold ~450 identical copies per key and exactly one dated point per country
    (#86, #95).

    The two are different questions. This one -- what is the figure now --
    is what the API serves. What the source has published over time is
    answered by refresh_indicator_history(), from the full series, and no
    longer as a side effect of filling a cache.

    The database session went with the history writing; nothing here touches
    the database now.
    """
    results: Dict[str, dict] = {}
    for name in COUNTRY_ISO3:
        invalidate_cache(name)
        data = get_country_esg_realtime(name)
        if data:
            results[name] = data

    return {"fetched": len(results), "total": len(COUNTRY_ISO3), "countries": results}


def refresh_live_data(trigger_source: str = "manual") -> Dict:
    """Background refresh with try/finally guarantee."""
    db = SessionLocal()
    _start = datetime.utcnow()
    log = DataRefreshLog(
        status="running", countries_fetched=0,
        total_countries=len(COUNTRY_ISO3), message=None,
        job_name="external_data_refresh", source="world_bank_oecd",
        started_at=_start, trigger_source=trigger_source,
    )
    db.add(log); db.commit(); db.refresh(log)
    result = None; exc = None
    try:
        _refresh_status["status"] = "running"
        result = refresh_all_countries()

        # The history is a separate pass over the same sources, storing the
        # full published series rather than the one figure the cache needs.
        # Its counts go into the log message: a run that says only "OK" is how
        # 408 empty OpenAQ runs passed for healthy (#56).
        #
        # Gated, because this job runs immediately when the scheduler starts:
        # enabling it by default would make the first mass ingestion a side
        # effect of a deployment. See history_refresh_enabled().
        if history_refresh_enabled():
            history = refresh_indicator_history(job_name="external_data_refresh")
        else:
            history = None
            logger.info(
                "indicator history refresh is off (SORA_HISTORY_REFRESH); "
                "the live cache was refreshed, the series was not")

        _refresh_status["last_refresh"] = datetime.utcnow().isoformat()
        _refresh_status["countries_refreshed"] = result["fetched"]
        log.status = "success"
        log.countries_fetched = result["fetched"]
        log.total_countries = result["total"]
        log.message = (
            "OK; history refresh disabled" if history is None else
            "OK; history fetched=%(fetched)d inserted=%(inserted)d "
            "unchanged=%(unchanged)d revised=%(revised)d "
            "no_value=%(no_value)d no_period=%(no_period)d"
            % history
        )
    except Exception as e:
        exc = e
        log.status = "error"
        log.message = str(e)[:500]
        logger.exception("refresh_live_data failed: %s", e)
    finally:
        _refresh_status["status"] = "idle"
        _end = datetime.utcnow()
        log.fetched_at = _end
        log.finished_at = _end
        log.duration_sec = round((_end - _start).total_seconds(), 2)
        try: db.commit()
        except Exception: db.rollback()
        finally: db.close()
    if exc is not None: raise exc
    return result

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
    val, src, _period = _fetch_with_fallback_impl(iso3, key, indicator_code, country_name)
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
    val, src, _period = _fetch_with_fallback_impl(iso3, key, indicator_code, country_name)
    if want_source:
        return val, src
    return val
