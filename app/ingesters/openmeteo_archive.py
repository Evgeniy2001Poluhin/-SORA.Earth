"""Open-Meteo archive backfill for environmental observations.

Fetches hourly historical weather and air quality data from Open-Meteo APIs to
fill gaps caused by server losses. These are NOT live readings: they are
reconstructed from later-fetched archives, stored under distinct source names
(openmeteo_archive, openmeteo_air_quality_archive) so they are never mistaken
for live ingestion.

The values come from the same sources as the live ingesters -- Open-Meteo's
weather models and CAMS reanalysis for air quality -- but fetched after the
fact using start_date/end_date parameters. Each Signal carries the archive hour
as event_time and "archive-fetched-<YYYY-MM-DD>" as source_revision, so
different fetch dates produce distinct vintages.

APIs:
- Weather: https://historical-forecast-api.open-meteo.com/v1/forecast
- Air quality: https://air-quality-api.open-meteo.com/v1/air-quality

Free-tier limits: 600 weighted API calls per minute, 10,000 calls per day.
Open-Meteo counts one request as ~2.6 calls per variable per month of range.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple, Optional, Dict

from app.ingesters.base import Signal
from app.ingesters.openmeteo import REGION_CAPITALS, WEATHER_VARIABLES, OpenMeteoIngester
from app.ingesters.openmeteo_air_quality import POLLUTANTS

log = logging.getLogger(__name__)

# API endpoints for historical data
WEATHER_ARCHIVE_ENDPOINT = "https://historical-forecast-api.open-meteo.com/v1/forecast"
AIR_QUALITY_ARCHIVE_ENDPOINT = "https://air-quality-api.open-meteo.com/v1/air-quality"

# Air quality archive coverage starts 2022-08-01
AIR_QUALITY_ARCHIVE_START = datetime(2022, 8, 1, tzinfo=timezone.utc)

# Rate limiting: Open-Meteo weights a request as roughly (days / 14) × (variables / 10).
# A one-year, 10-variable request is ≈ (365 / 14) × (10 / 10) = 26 weighted calls.
# Free tier limits: 600 weighted calls/min, 5,000/hour, 10,000/day.
# With 5s between requests (12 req/min) that is ≈ 312 weighted calls per minute (under 600/min)
# but ≈ 18,700 per hour, so the hourly limit (5,000) is reached after ~16 minutes
# and the daily (10,000) after ~32 — which is why API mode refuses estimates above 4,000
# and long ranges must use --from-dir.
REQUEST_DELAY_SECONDS = 5.0
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2
RATE_LIMIT_RETRY_WAIT = 60  # Initial wait on HTTP 429


def _build_revision_tag(fetch_or_vintage_date: datetime) -> str:
    """Build source_revision tag for archive fetch.

    Format: archive-fetched-<YYYY-MM-DD> (UTC date of the fetch or --vintage).
    This makes re-fetches on different dates produce distinct vintages.
    """
    return f"archive-fetched-{fetch_or_vintage_date.strftime('%Y-%m-%d')}"


def _fetch_weather_for_point(
    region_code: str,
    lat: float,
    lon: float,
    start_date: str,
    end_date: str,
    retries: int = MAX_RETRIES,
) -> Optional[dict]:
    """Fetch hourly weather for one point and date range.

    Args:
        region_code: Region identifier (e.g., "DEU", "RU-MOW")
        lat: Latitude
        lon: Longitude
        start_date: Start date (YYYY-MM-DD, inclusive)
        end_date: End date (YYYY-MM-DD, inclusive)
        retries: Number of retry attempts on failure

    Returns:
        API response dict, or None on failure after retries
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": ",".join(WEATHER_VARIABLES),
        "timezone": "UTC",
    }

    query_string = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{WEATHER_ARCHIVE_ENDPOINT}?{query_string}"

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                data = json.loads(response.read().decode())
                log.debug(f"[weather_archive] {region_code}: fetched {start_date} to {end_date}")
                return data
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = RATE_LIMIT_RETRY_WAIT * (2 ** attempt)
                log.warning(f"[weather_archive] {region_code} rate limited (429), waiting {wait}s")
                time.sleep(wait)
                continue
            wait = RETRY_BACKOFF_BASE ** attempt
            log.warning(f"[weather_archive] {region_code} HTTP {e.code} attempt {attempt + 1}/{retries}: {e}; wait {wait}s")
            if attempt < retries - 1:
                time.sleep(wait)
            else:
                log.error(f"[weather_archive] {region_code} failed after {retries} attempts")
                return None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            wait = RETRY_BACKOFF_BASE ** attempt
            log.warning(f"[weather_archive] {region_code} attempt {attempt + 1}/{retries} failed: {e}; wait {wait}s")
            if attempt < retries - 1:
                time.sleep(wait)
            else:
                log.error(f"[weather_archive] {region_code} failed after {retries} attempts")
                return None
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            log.error(f"[weather_archive] {region_code} malformed response: {e}")
            return None

    return None


def _fetch_air_quality_for_point(
    region_code: str,
    lat: float,
    lon: float,
    start_date: str,
    end_date: str,
    retries: int = MAX_RETRIES,
) -> Optional[dict]:
    """Fetch hourly air quality for one point and date range.

    Args:
        region_code: Region identifier
        lat: Latitude
        lon: Longitude
        start_date: Start date (YYYY-MM-DD, inclusive)
        end_date: End date (YYYY-MM-DD, inclusive)
        retries: Number of retry attempts on failure

    Returns:
        API response dict, or None on failure after retries
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": ",".join(POLLUTANTS.keys()),
        "timezone": "UTC",
    }

    query_string = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{AIR_QUALITY_ARCHIVE_ENDPOINT}?{query_string}"

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                data = json.loads(response.read().decode())
                log.debug(f"[air_quality_archive] {region_code}: fetched {start_date} to {end_date}")
                return data
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = RATE_LIMIT_RETRY_WAIT * (2 ** attempt)
                log.warning(f"[air_quality_archive] {region_code} rate limited (429), waiting {wait}s")
                time.sleep(wait)
                continue
            wait = RETRY_BACKOFF_BASE ** attempt
            log.warning(f"[air_quality_archive] {region_code} HTTP {e.code} attempt {attempt + 1}/{retries}: {e}; wait {wait}s")
            if attempt < retries - 1:
                time.sleep(wait)
            else:
                log.error(f"[air_quality_archive] {region_code} failed after {retries} attempts")
                return None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            wait = RETRY_BACKOFF_BASE ** attempt
            log.warning(f"[air_quality_archive] {region_code} attempt {attempt + 1}/{retries} failed: {e}; wait {wait}s")
            if attempt < retries - 1:
                time.sleep(wait)
            else:
                log.error(f"[air_quality_archive] {region_code} failed after {retries} attempts")
                return None
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            log.error(f"[air_quality_archive] {region_code} malformed response: {e}")
            return None

    return None


def _parse_weather_response(
    data: dict,
    region_code: str,
    lat: float,
    lon: float,
    source_revision: str,
    chunk_start: Optional[datetime] = None,
    chunk_end: Optional[datetime] = None,
) -> Tuple[List[Signal], int]:
    """Parse weather API response into Signal objects.

    Args:
        data: API response dict
        region_code: Region identifier
        lat: Latitude
        lon: Longitude
        source_revision: Revision tag for this fetch
        chunk_start: If provided, filter to rows >= this time
        chunk_end: If provided, filter to rows <= this time

    Returns:
        Tuple of (signals, none_count)
    """
    signals = []
    none_count = 0

    hourly = data.get("hourly", {})
    if not hourly:
        log.warning(f"[weather_archive] {region_code}: no hourly data in response")
        return signals, none_count

    times = hourly.get("time", [])
    if not times:
        log.warning(f"[weather_archive] {region_code}: no time array in hourly data")
        return signals, none_count

    for var in WEATHER_VARIABLES:
        values = hourly.get(var, [])
        if not values:
            continue

        metric_name, unit = OpenMeteoIngester._map_variable(var)

        for i, time_str in enumerate(times):
            if i >= len(values):
                break

            value = values[i]
            if value is None:
                none_count += 1
                continue

            try:
                event_time = datetime.fromisoformat(time_str).replace(tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                log.warning(f"[weather_archive] {region_code}: invalid time {time_str}")
                continue

            if chunk_start and event_time < chunk_start:
                continue
            if chunk_end and event_time > chunk_end:
                continue

            signals.append(Signal(
                region_code=region_code,
                source="openmeteo_archive",
                metric=metric_name,
                value=float(value),
                unit=unit,
                observed_at=event_time,
                temporal_kind="observed",
                source_revision=source_revision,
                metadata={"latitude": lat, "longitude": lon},
            ))

    return signals, none_count


def _parse_air_quality_response(
    data: dict,
    region_code: str,
    lat: float,
    lon: float,
    source_revision: str,
    chunk_start: Optional[datetime] = None,
    chunk_end: Optional[datetime] = None,
) -> Tuple[List[Signal], int]:
    """Parse air quality API response into Signal objects.

    Args:
        data: API response dict
        region_code: Region identifier
        lat: Latitude
        lon: Longitude
        source_revision: Revision tag for this fetch
        chunk_start: If provided, filter to rows >= this time
        chunk_end: If provided, filter to rows <= this time

    Returns:
        Tuple of (signals, none_count)
    """
    signals = []
    none_count = 0

    hourly = data.get("hourly", {})
    if not hourly:
        log.warning(f"[air_quality_archive] {region_code}: no hourly data in response")
        return signals, none_count

    times = hourly.get("time", [])
    if not times:
        log.warning(f"[air_quality_archive] {region_code}: no time array in hourly data")
        return signals, none_count

    for pollutant, unit in POLLUTANTS.items():
        values = hourly.get(pollutant, [])
        if not values:
            continue

        for i, time_str in enumerate(times):
            if i >= len(values):
                break

            value = values[i]
            if value is None:
                none_count += 1
                continue

            try:
                numeric = float(value)
            except (TypeError, ValueError):
                log.warning(f"[air_quality_archive] {region_code} {pollutant}: non-numeric value {value!r}")
                none_count += 1
                continue

            try:
                event_time = datetime.fromisoformat(time_str).replace(tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                log.warning(f"[air_quality_archive] {region_code}: invalid time {time_str}")
                continue

            if chunk_start and event_time < chunk_start:
                continue
            if chunk_end and event_time > chunk_end:
                continue

            signals.append(Signal(
                region_code=region_code,
                source="openmeteo_air_quality_archive",
                metric=pollutant,
                value=numeric,
                unit=unit,
                observed_at=event_time,
                temporal_kind="observed",
                source_revision=source_revision,
                metadata={
                    "measurement_kind": "modelled",
                    "model": "CAMS reanalysis via Open-Meteo",
                    "latitude": lat,
                    "longitude": lon,
                },
            ))

    return signals, none_count


def _parse_year_from_filename(filename: str) -> Optional[int]:
    """Extract year from archive filename pattern <POINT>_<YEAR>.json.gz.

    Args:
        filename: Filename like "DEU_2024.json.gz" or "RU-MOW_2023.json.gz"

    Returns:
        Year as int, or None if pattern doesn't match
    """
    match = re.match(r'^[A-Z]{3}(?:-[A-Z]{3})?_(\d{4})\.json\.gz$', filename)
    if match:
        return int(match.group(1))
    return None


def _load_cached_weather_for_chunk(
    directory: Path,
    region_code: str,
    chunk_start: datetime,
    chunk_end: datetime,
) -> Tuple[List[dict], List[str]]:
    """Load cached weather responses for a chunk from yearly archive files.

    Reads all files weather/<region_code>_<YEAR>.json.gz where YEAR overlaps
    [chunk_start.year, chunk_end.year].

    Args:
        directory: Base directory containing weather/ subdirectory
        region_code: Region identifier
        chunk_start: Chunk start time
        chunk_end: Chunk end time

    Returns:
        Tuple of (responses, file_paths_read)
    """
    weather_dir = directory / "weather"
    if not weather_dir.exists():
        return [], []

    responses = []
    files_read = []

    for year in range(chunk_start.year, chunk_end.year + 1):
        filename = f"{region_code}_{year}.json.gz"
        filepath = weather_dir / filename

        if not filepath.exists():
            continue

        try:
            with gzip.open(filepath, "rt", encoding="utf-8") as f:
                data = json.load(f)
                responses.append(data)
                files_read.append(str(filepath))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
            log.error(f"[weather_archive] failed to load {filepath}: {e}")

    return responses, files_read


def _load_cached_air_quality_for_chunk(
    directory: Path,
    region_code: str,
    chunk_start: datetime,
    chunk_end: datetime,
) -> Tuple[List[dict], List[str]]:
    """Load cached air quality responses for a chunk from yearly archive files.

    Reads all files air_quality/<region_code>_<YEAR>.json.gz where YEAR overlaps
    [chunk_start.year, chunk_end.year].

    Args:
        directory: Base directory containing air_quality/ subdirectory
        region_code: Region identifier
        chunk_start: Chunk start time
        chunk_end: Chunk end time

    Returns:
        Tuple of (responses, file_paths_read)
    """
    aq_dir = directory / "air_quality"
    if not aq_dir.exists():
        return [], []

    responses = []
    files_read = []

    for year in range(chunk_start.year, chunk_end.year + 1):
        filename = f"{region_code}_{year}.json.gz"
        filepath = aq_dir / filename

        if not filepath.exists():
            continue

        try:
            with gzip.open(filepath, "rt", encoding="utf-8") as f:
                data = json.load(f)
                responses.append(data)
                files_read.append(str(filepath))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
            log.error(f"[air_quality_archive] failed to load {filepath}: {e}")

    return responses, files_read


def fetch_weather_archive(
    start_date: datetime,
    end_date: datetime,
    from_dir: Optional[Path] = None,
    vintage: Optional[datetime] = None,
) -> Tuple[List[Signal], Dict[str, any]]:
    """Fetch hourly weather archive for all points in date range.

    Args:
        start_date: Start date (inclusive, UTC)
        end_date: End date (inclusive, UTC)
        from_dir: If provided, read cached responses from this directory
        vintage: With from_dir, the download date for source_revision

    Returns:
        Tuple of (signals, stats_dict)
        stats_dict has: points_fetched, signals_produced, none_values_skipped,
                        files_read (with from_dir), missing_years (with from_dir)
    """
    signals = []
    none_count = 0
    fetch_date = vintage if vintage else datetime.now(timezone.utc)
    source_revision = _build_revision_tag(fetch_date)

    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    files_read = []
    missing_years = []

    for region_code, lat, lon in REGION_CAPITALS:
        if from_dir:
            responses, point_files = _load_cached_weather_for_chunk(
                from_dir, region_code, start_date, end_date
            )
            files_read.extend(point_files)

            if not responses:
                for year in range(start_date.year, end_date.year + 1):
                    missing_years.append((region_code, year, "weather"))
                log.warning(f"[weather_archive] {region_code}: no cached data for {start_date.year}-{end_date.year}")
                continue

            for data in responses:
                parsed, nones = _parse_weather_response(
                    data, region_code, lat, lon, source_revision,
                    chunk_start=start_date, chunk_end=end_date
                )
                signals.extend(parsed)
                none_count += nones
        else:
            data = _fetch_weather_for_point(region_code, lat, lon, start_str, end_str)
            if data is None:
                continue
            time.sleep(REQUEST_DELAY_SECONDS)

            parsed, nones = _parse_weather_response(data, region_code, lat, lon, source_revision)
            signals.extend(parsed)
            none_count += nones

    stats = {
        "points_fetched": len(REGION_CAPITALS),
        "signals_produced": len(signals),
        "none_values_skipped": none_count,
    }

    if from_dir:
        stats["files_read"] = files_read
        stats["missing_years"] = missing_years

    log.info(f"[weather_archive] {stats['signals_produced']} signals, {none_count} None values skipped")
    return signals, stats


def fetch_air_quality_archive(
    start_date: datetime,
    end_date: datetime,
    from_dir: Optional[Path] = None,
    vintage: Optional[datetime] = None,
) -> Tuple[List[Signal], Dict[str, any]]:
    """Fetch hourly air quality archive for all points in date range.

    Args:
        start_date: Start date (inclusive, UTC)
        end_date: End date (inclusive, UTC)
        from_dir: If provided, read cached responses from this directory
        vintage: With from_dir, the download date for source_revision

    Returns:
        Tuple of (signals, stats_dict)
        stats_dict has: points_fetched, signals_produced, none_values_skipped,
                        files_read (with from_dir), missing_years (with from_dir)
    """
    signals = []
    none_count = 0
    fetch_date = vintage if vintage else datetime.now(timezone.utc)
    source_revision = _build_revision_tag(fetch_date)

    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    files_read = []
    missing_years = []

    for region_code, lat, lon in REGION_CAPITALS:
        if from_dir:
            responses, point_files = _load_cached_air_quality_for_chunk(
                from_dir, region_code, start_date, end_date
            )
            files_read.extend(point_files)

            if not responses:
                for year in range(start_date.year, end_date.year + 1):
                    if datetime(year, 1, 1, tzinfo=timezone.utc) >= AIR_QUALITY_ARCHIVE_START:
                        missing_years.append((region_code, year, "air_quality"))
                log.warning(f"[air_quality_archive] {region_code}: no cached data for {start_date.year}-{end_date.year}")
                continue

            for data in responses:
                parsed, nones = _parse_air_quality_response(
                    data, region_code, lat, lon, source_revision,
                    chunk_start=start_date, chunk_end=end_date
                )
                signals.extend(parsed)
                none_count += nones
        else:
            data = _fetch_air_quality_for_point(region_code, lat, lon, start_str, end_str)
            if data is None:
                continue
            time.sleep(REQUEST_DELAY_SECONDS)

            parsed, nones = _parse_air_quality_response(data, region_code, lat, lon, source_revision)
            signals.extend(parsed)
            none_count += nones

    stats = {
        "points_fetched": len(REGION_CAPITALS),
        "signals_produced": len(signals),
        "none_values_skipped": none_count,
    }

    if from_dir:
        stats["files_read"] = files_read
        stats["missing_years"] = missing_years

    log.info(f"[air_quality_archive] {stats['signals_produced']} signals, {none_count} None values skipped")
    return signals, stats




def fetch_weather_for_point_year(
    region_code: str,
    lat: float,
    lon: float,
    year: int,
    chunk_start: datetime,
    chunk_end: datetime,
    from_dir: Optional[Path] = None,
    vintage: Optional[datetime] = None,
) -> Tuple[List[Signal], Dict[str, any]]:
    """Fetch hourly weather for one point and one year.

    Args:
        region_code: Region identifier
        lat: Latitude
        lon: Longitude
        year: Year to fetch
        chunk_start: Start datetime (for filtering)
        chunk_end: End datetime (for filtering)
        from_dir: If provided, read cached responses from this directory
        vintage: With from_dir, the download date for source_revision

    Returns:
        Tuple of (signals, stats_dict)
    """
    signals = []
    none_count = 0
    fetch_date = vintage if vintage else datetime.now(timezone.utc)
    source_revision = _build_revision_tag(fetch_date)

    files_read = []
    missing_years = []

    if from_dir:
        responses, point_files = _load_cached_weather_for_chunk(
            from_dir, region_code, chunk_start, chunk_end
        )
        files_read.extend(point_files)

        if not responses:
            missing_years.append((region_code, year, "weather"))
            log.warning(f"[weather_archive] {region_code} year {year}: no cached data")
        else:
            for data in responses:
                parsed, nones = _parse_weather_response(
                    data, region_code, lat, lon, source_revision,
                    chunk_start=chunk_start, chunk_end=chunk_end
                )
                signals.extend(parsed)
                none_count += nones
    else:
        # API fetch for this year
        year_start = max(chunk_start, datetime(year, 1, 1, tzinfo=timezone.utc))
        year_end = min(chunk_end, datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc))
        
        start_str = year_start.strftime("%Y-%m-%d")
        end_str = year_end.strftime("%Y-%m-%d")
        
        data = _fetch_weather_for_point(region_code, lat, lon, start_str, end_str)
        if data is not None:
            parsed, nones = _parse_weather_response(
                data, region_code, lat, lon, source_revision,
                chunk_start=chunk_start, chunk_end=chunk_end
            )
            signals.extend(parsed)
            none_count += nones
            time.sleep(REQUEST_DELAY_SECONDS)

    stats = {
        "signals_produced": len(signals),
        "none_values_skipped": none_count,
        "files_read": files_read,
        "missing_years": missing_years,
    }

    return signals, stats


def fetch_air_quality_for_point_year(
    region_code: str,
    lat: float,
    lon: float,
    year: int,
    chunk_start: datetime,
    chunk_end: datetime,
    from_dir: Optional[Path] = None,
    vintage: Optional[datetime] = None,
) -> Tuple[List[Signal], Dict[str, any]]:
    """Fetch hourly air quality for one point and one year.

    Args:
        region_code: Region identifier
        lat: Latitude
        lon: Longitude
        year: Year to fetch
        chunk_start: Start datetime (for filtering)
        chunk_end: End datetime (for filtering)
        from_dir: If provided, read cached responses from this directory
        vintage: With from_dir, the download date for source_revision

    Returns:
        Tuple of (signals, stats_dict)
    """
    signals = []
    none_count = 0
    fetch_date = vintage if vintage else datetime.now(timezone.utc)
    source_revision = _build_revision_tag(fetch_date)

    files_read = []
    missing_years = []

    if from_dir:
        responses, point_files = _load_cached_air_quality_for_chunk(
            from_dir, region_code, chunk_start, chunk_end
        )
        files_read.extend(point_files)

        if not responses:
            if datetime(year, 1, 1, tzinfo=timezone.utc) >= AIR_QUALITY_ARCHIVE_START:
                missing_years.append((region_code, year, "air_quality"))
            log.warning(f"[air_quality_archive] {region_code} year {year}: no cached data")
        else:
            for data in responses:
                parsed, nones = _parse_air_quality_response(
                    data, region_code, lat, lon, source_revision,
                    chunk_start=chunk_start, chunk_end=chunk_end
                )
                signals.extend(parsed)
                none_count += nones
    else:
        # API fetch for this year
        year_start = max(chunk_start, datetime(year, 1, 1, tzinfo=timezone.utc))
        year_end = min(chunk_end, datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc))
        
        start_str = year_start.strftime("%Y-%m-%d")
        end_str = year_end.strftime("%Y-%m-%d")
        
        data = _fetch_air_quality_for_point(region_code, lat, lon, start_str, end_str)
        if data is not None:
            parsed, nones = _parse_air_quality_response(
                data, region_code, lat, lon, source_revision,
                chunk_start=chunk_start, chunk_end=chunk_end
            )
            signals.extend(parsed)
            none_count += nones
            time.sleep(REQUEST_DELAY_SECONDS)

    stats = {
        "signals_produced": len(signals),
        "none_values_skipped": none_count,
        "files_read": files_read,
        "missing_years": missing_years,
    }

    return signals, stats


def compute_archive_hash(files_read: List[str]) -> str:
    """Compute sha256 over sorted file paths and their content hashes.

    Args:
        files_read: List of file paths that were read

    Returns:
        Hex digest of combined hash
    """
    if not files_read:
        return ""

    file_hashes = []
    for filepath in sorted(files_read):
        try:
            with open(filepath, "rb") as f:
                content_hash = hashlib.sha256(f.read()).hexdigest()
                file_hashes.append(f"{filepath}:{content_hash}")
        except OSError:
            pass

    combined = "\n".join(file_hashes).encode()
    return hashlib.sha256(combined).hexdigest()
