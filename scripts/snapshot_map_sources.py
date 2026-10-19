#!/usr/bin/env python3
"""Snapshot the public sources behind the map's country inputs, as raw bytes.

The map scores each country from five inputs: renewable share, CO2 per capita,
the Gini index, government effectiveness and the Human Development Index. Until
now their provenance was a sentence in app/country_benchmarks.py, and two of
the five had no source at all. This script fetches the public series once,
keeps every response as the bytes the server sent, and records what each value
says about itself, so that a later step can say where a number came from and
how old it is.

It does not choose a value, drop a country, or score anything. Turning a
snapshot into per-country inputs is scripts/build_map_inputs.py, which runs
offline against a snapshot this script wrote.

Sources (the codes are pinned here, and tests check them against the app):

  gini              World Bank SI.POV.GINI, Gini index (survey-based, sparse years)
  renewables        World Bank EG.FEC.RNEW.ZS (the default, see --renewables)
                    or EG.ELC.RNEW.ZS. EXACTLY ONE per snapshot.
  co2_per_capita    World Bank EN.GHG.CO2.PC.CE.AR5, the series app/country_benchmarks.py
                    names as its CO2 source. EN.ATM.CO2E.PC is not used: the app
                    records it as absent from the World Bank API (#97).
  gov_effectiveness World Bank WGI Government Effectiveness, estimate scale. The
                    code is GOV_WGI_GE.EST (see GOV_WGI_CODE below); the
                    percentile rank is not used because the score formula
                    expects the estimate, roughly -2.5 to +2.5.
  hdi               UNDP Human Development Report composite indices, time series
                    CSV, linked from hdr.undp.org/data-center/documentation-and-downloads.
                    The newest release on that page is chosen by the year in its URL.

Every World Bank series is requested for the 33 countries of the map in one
request per page (country/ISO3;ISO3;...), so a country's figures are never
mixed with an aggregate or a region.

What is kept, under <root>/<UTC date>/:

  manifest.json          every request: URL, parameters, status, bytes, sha256,
                         the rows and totals it reported, the indicator
                         metadata (name, source id), the World Bank lastupdated
                         stamp, the UNDP release, fetched_at, the latest year
                         per country, and a snapshot digest over every raw file
  raw/<indicator>/page-NNNNN.json   the World Bank pages, as received
  raw/hdi/...csv                    the UNDP CSV, as received
  raw/undp-documentation-and-downloads.html   the page the UNDP link came from

What makes the run refuse (no final directory is created):

  * a page whose meta.page or meta.pages does not follow in sequence;
  * a total that changes between pages, or rows that do not add up to the
    reported total (incomplete pages);
  * a country code the request did not ask for, or a row for an indicator
    other than the one requested;
  * the same (country, year) twice in one series (duplicates);
  * a value outside its indicator's range: a Gini outside 0-100, a share outside
    0-100, CO2 outside 0-100 tonnes, a WGI estimate outside -3..3, an HDI
    outside 0-1;
  * an indicator name that does not say what the code is supposed to measure
    (for example a "%" share whose name has no "%");
  * a response that is HTML, or not JSON or CSV, where data was expected;
  * a World Bank error body (the API answers 200 with a "message" for an unknown
    code);
  * an indicator whose metadata names a source other than the one expected;
  * a UNDP page with no composite-index CSV on it;
  * a snapshot in which a series has no value at all;
  * an output directory that exists, or a leftover <date>.partial from an
    earlier run (nothing is ever overwritten or deleted);
  * an fsync that fails before the rename.

A refused or failed run leaves <date>.partial for inspection and creates no
<date>. The previous snapshot directory is never opened for writing, so it is
still the one to build from: scripts/build_map_inputs.py --latest-of ROOT picks
the newest directory that verifies, and ignores *.partial.

Politeness. At most one request per second. HTTP 429, 5xx and connection
errors are retried, up to three attempts, with waits of 60 s and then 120 s.
Other 4xx responses are not retried.

Usage (from the repository root):

    python3 scripts/snapshot_map_sources.py --probe
    python3 scripts/snapshot_map_sources.py
    python3 scripts/snapshot_map_sources.py --renewables electricity --only renewables --root ../scratch

--probe fetches the metadata of the candidate codes, the first page of each
series, and the UNDP link list. It writes nothing.
"""
import argparse
import csv
import datetime as _dt
import hashlib
import io
import json
import math
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

ROOT = Path(__file__).resolve().parent.parent
TOOL_NAME = "scripts/snapshot_map_sources.py"
DEFAULT_ROOT = Path("/Users/evgenijpoluhin/sora-research/map-sources")

WB_BASE = "https://api.worldbank.org/v2"
UNDP_DOCS_URL = "https://hdr.undp.org/data-center/documentation-and-downloads"
REQUEST_TIMEOUT_S = 120
MIN_INTERVAL_S = 1.0
RETRY_ATTEMPTS = 3
BACKOFF_BASE_S = 60
RETRYABLE_STATUS = frozenset({429, *range(500, 600)})
WB_PER_PAGE = 1000

# The 33 countries app/api/map_data.py COUNTRIES offers, by ISO3. Checked
# against that list by tests/test_map_sources_snapshot.py.
MAP_ISO3 = (
    "SWE", "NOR", "FIN", "DNK", "ISL", "DEU", "FRA", "GBR", "NLD", "CHE", "AUT", "BEL",
    "ESP", "ITA", "PRT", "POL", "CZE", "USA", "CAN", "MEX", "BRA", "ARG", "CHL", "JPN",
    "KOR", "CHN", "IND", "AUS", "NZL", "ZAF", "KEN", "ARE", "RUS",
)

# The renewables indicator the snapshot uses by default. The choice and its
# justification are in the build script's policy and in the commit message.
RENEWABLE_CODES = {
    "final": "EG.FEC.RNEW.ZS",       # % of total final energy consumption
    "electricity": "EG.ELC.RNEW.ZS",  # % of total electricity output
}
DEFAULT_RENEWABLES = "final"

# WGI Government Effectiveness, estimate. Confirmed against the World Bank
# indicator metadata by --probe; see GOV_WGI_CANDIDATES.
GOV_WGI_CODE = "GOV_WGI_GE.EST"
GOV_WGI_CANDIDATES = ("GOV_WGI_GE.EST", "GE.EST")  # GE.EST is the archived copy, source 57

WDI_SOURCE = "World Development Indicators"  # the source the WDI series are published under

# name_must_contain: a token the indicator's own name must carry, so a code that
# has moved to a different series is refused rather than read as a value.
# expected_source: the substring the World Bank metadata source name must carry.
WB_SPECS = {
    "gini": {
        "label": "Gini index (World Bank SI.POV.GINI)",
        "unit": "index, 0-100 (survey-based, sparse years)",
        "range": (0.0, 100.0),
        "name_must_contain": "Gini",
        "expected_source": WDI_SOURCE,
    },
    "co2_per_capita": {
        "label": "CO2 emissions per capita (World Bank EN.GHG.CO2.PC.CE.AR5)",
        "unit": "t CO2 per capita",
        "range": (0.0, 100.0),
        "name_must_contain": "per capita",
        "expected_source": WDI_SOURCE,
    },
    "gov_effectiveness": {
        "label": "Government Effectiveness, WGI estimate",
        "unit": "WGI estimate (approx. -2.5 to +2.5)",
        "range": (-3.0, 3.0),
        "name_must_contain": "Government Effectiveness",
        "expected_source": "Governance",
    },
}
CO2_CODE = "EN.GHG.CO2.PC.CE.AR5"
GINI_CODE = "SI.POV.GINI"
RENEWABLE_NAME_TOKEN = "%"
HDI_RANGE = (0.0, 1.0)
HDI_COLUMN = re.compile(r"^hdi_(\d{4})$")
YEAR = re.compile(r"^\d{4}$")

SNAPSHOT_DIGEST_METHOD = (
    "sha256 of the UTF-8 text made by sorting the lines '<sha256>  <path relative to the "
    "snapshot directory>' over every raw file, each followed by a newline. manifest.json "
    "is not included."
)

# Read at call time, never bound as default arguments, so tests can replace them.
_clock = time.monotonic
_sleep = time.sleep


class SnapshotError(RuntimeError):
    """The snapshot cannot be finished honestly. The message says what, and where."""


def _utc_now():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_date():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


def _git(*args):
    try:
        done = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip()


def _tool_info():
    sha = _git("rev-parse", "HEAD")
    porcelain = _git("status", "--porcelain")
    return {
        "name": TOOL_NAME,
        "git_sha": sha or None,
        "git_dirty": None if porcelain is None else porcelain != "",
        "python": platform.python_version(),
    }


def _as_int(value, label, what):
    if isinstance(value, bool):
        raise SnapshotError(f"{label}: {what} is a boolean, not a count")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise SnapshotError(f"{label}: {what} is not a count: {value!r}")


def _looks_like_html(body):
    head = body.lstrip()[:200].lower()
    return head.startswith(b"<") or b"<html" in head or b"<!doctype" in head


def _load_json(body, label):
    if _looks_like_html(body):
        raise SnapshotError(f"{label}: the response is HTML, not JSON")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SnapshotError(f"{label}: body is not JSON ({type(exc).__name__}: {exc})") from None


def _check_range(label, value, bounds, what):
    lo, hi = bounds
    if not math.isfinite(value) or value < lo or value > hi:
        raise SnapshotError(f"{label}: {what} {value!r} is outside {lo:g}..{hi:g}")


# ---------------------------------------------------------------------------
# Parsing and validation. Pure functions over bytes: the tests and the build
# script call these, so the build re-applies every refusal to the raw files.
# ---------------------------------------------------------------------------

def _check_not_future(label, iso3, year, snapshot_year):
    """A value for a year after the snapshot's own year is refused, never selected.

    A snapshot dated 2026 cannot hold an observation for 2027: the figure does not
    exist yet, so a row for it is a defect in the source or in the bytes, and its
    age would be negative. A null value for a future year is not an observation and
    is not refused here.
    """
    if snapshot_year is not None and int(year) > snapshot_year:
        raise SnapshotError(
            f"{label}: {iso3} has a value for {year}, after the snapshot year {snapshot_year}; "
            "an observation cannot postdate the snapshot that holds it. Refusing."
        )


def parse_wb_page(body, label, code, spec, requested, snapshot_year=None):
    """One World Bank page. Returns its meta and its rows, each checked on its own.

    `spec` is the entry of WB_SPECS (or a renewables entry) for `code`. The rows
    are dicts with iso3, year, value (None when the source has no value for that
    year) and indicator_name. Cross-page checks are in assemble_wb_series.
    """
    payload = _load_json(body, label)
    if isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], dict) \
            and "message" in payload[0]:
        raise SnapshotError(f"{label}: the World Bank answered with an error: {payload[0]['message']!r}")
    if not (isinstance(payload, list) and len(payload) == 2 and isinstance(payload[0], dict)):
        raise SnapshotError(f"{label}: the body is not the World Bank [meta, rows] pair")
    meta = payload[0]
    page = _as_int(meta.get("page"), label, "meta.page")
    pages = _as_int(meta.get("pages"), label, "meta.pages")
    total = _as_int(meta.get("total"), label, "meta.total")
    rows = payload[1] if payload[1] is not None else []
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise SnapshotError(f"{label}: the rows are not a list of objects")

    parsed = []
    names = set()
    for index, row in enumerate(rows):
        indicator = row.get("indicator") or {}
        if indicator.get("id") != code:
            raise SnapshotError(
                f"{label}: row {index} is for indicator {indicator.get('id')!r}, not {code!r}"
            )
        iso3 = row.get("countryiso3code")
        if iso3 not in requested:
            raise SnapshotError(
                f"{label}: row {index} is for country {iso3!r}, which was not requested"
            )
        year = row.get("date")
        if not isinstance(year, str) or not YEAR.match(year):
            raise SnapshotError(f"{label}: row {index} ({iso3}) has no four-digit year ({year!r})")
        value = row.get("value")
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SnapshotError(f"{label}: row {index} ({iso3} {year}) value is not a number: {value!r}")
            value = float(value)
            _check_range(label, value, spec["range"], f"{iso3} {year} value")
            _check_not_future(label, iso3, year, snapshot_year)
        name = indicator.get("value") or ""
        names.add(name)
        parsed.append({"iso3": iso3, "year": year, "value": value, "indicator_name": name})

    if names and len(names) > 1:
        raise SnapshotError(f"{label}: the rows carry {len(names)} indicator names; one series expected")
    name = next(iter(names)) if names else ""
    if rows and spec["name_must_contain"] not in name:
        raise SnapshotError(
            f"{label}: the indicator is named {name!r}, which does not contain "
            f"{spec['name_must_contain']!r}; the code may no longer be the series it was chosen for"
        )
    return {
        "page": page,
        "pages": pages,
        "total": total,
        "lastupdated": meta.get("lastupdated"),
        "rows": parsed,
        "indicator_name": name,
    }


def assemble_wb_series(pages, label):
    """Check the pages of one series against each other, and return its rows.

    `pages` is a list of parse_wb_page results in request order. Refuses an
    incomplete series (a page missing, or rows that do not add up to the reported
    total), a series whose total moves between pages, and the same
    (country, year) twice.
    """
    if not pages:
        raise SnapshotError(f"{label}: no pages")
    total = pages[0]["total"]
    pages_expected = pages[0]["pages"]
    rows = []
    for number, page in enumerate(pages, start=1):
        where = f"{label} page {number}"
        if page["page"] != number:
            raise SnapshotError(f"{where}: meta.page is {page['page']}, expected {number}")
        if page["pages"] != pages_expected:
            raise SnapshotError(f"{where}: meta.pages changed from {pages_expected} to {page['pages']}")
        if page["total"] != total:
            raise SnapshotError(f"{where}: meta.total changed from {total} to {page['total']} during the run")
        rows.extend(page["rows"])
    # One release per series. Each page carries the World Bank's lastupdated stamp; pages
    # that disagree mix two releases of the same indicator, and a page with no stamp
    # cannot be pinned to any release at all. Both are refused, never reconciled.
    stamps = [page["lastupdated"] for page in pages]
    if any(not stamp for stamp in stamps):
        raise SnapshotError(
            f"{label}: a page carries no lastupdated release stamp; the series cannot be pinned to one release"
        )
    if len(set(stamps)) > 1:
        raise SnapshotError(
            f"{label}: the pages carry {len(set(stamps))} release stamps "
            f"({', '.join(sorted(set(stamps)))}); a series must come from one release. Refusing to mix them."
        )
    if len(pages) != pages_expected:
        raise SnapshotError(f"{label}: {len(pages)} pages fetched, meta.pages says {pages_expected}")
    if len(rows) != total:
        raise SnapshotError(
            f"{label}: the pages hold {len(rows)} rows but meta.total is {total}; the series is incomplete"
        )
    seen = set()
    for row in rows:
        key = (row["iso3"], row["year"])
        if key in seen:
            raise SnapshotError(f"{label}: {row['iso3']} {row['year']} appears twice; duplicates are refused")
        seen.add(key)
    return rows


def parse_undp_csv(body, label, requested, snapshot_year=None):
    """The UNDP composite-indices time series. Returns {iso3: {year: value or None}}.

    Only the hdi_YYYY columns are read; the other indices are not used. A row
    without an ISO3 code (an aggregate) is skipped. The same ISO3 twice is a
    refusal, anywhere in the file.
    """
    if _looks_like_html(body):
        raise SnapshotError(f"{label}: the response is HTML, not CSV")
    # UTF-8 first. The 2025 file carries country names in a legacy encoding, so
    # cp1252 is the fallback; the fields read here are ASCII either way. The
    # encoding used is returned and recorded, never assumed silently.
    try:
        text, encoding = body.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        try:
            text, encoding = body.decode("cp1252"), "cp1252"
        except UnicodeDecodeError as exc:
            raise SnapshotError(f"{label}: the file is neither UTF-8 nor cp1252 ({exc})") from None
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []
    if "iso3" not in fields:
        raise SnapshotError(f"{label}: there is no iso3 column")
    year_columns = {}
    for field in fields:
        match = HDI_COLUMN.match(field or "")
        if match:
            year_columns[field] = match.group(1)
    if not year_columns:
        raise SnapshotError(f"{label}: there are no hdi_YYYY columns")

    table = {}
    for number, row in enumerate(reader, start=2):
        iso3 = (row.get("iso3") or "").strip()
        if not iso3:
            continue
        if iso3 in table:
            raise SnapshotError(f"{label}: {iso3} appears twice (line {number}); duplicates are refused")
        values = {}
        for column, year in year_columns.items():
            raw = (row.get(column) or "").strip()
            if raw == "":
                values[year] = None
                continue
            try:
                value = float(raw)
            except ValueError:
                raise SnapshotError(f"{label}: {iso3} {year} is not a number: {raw!r}") from None
            _check_not_future(label, iso3, year, snapshot_year)
            _check_range(label, value, HDI_RANGE, f"{iso3} hdi_{year}")
            values[year] = value
        table[iso3] = values
    missing = sorted(set(requested) - set(table))
    # A country the file does not carry is recorded as absent by the build, not refused.
    return {
        "table": table,
        "years": sorted(year_columns.values()),
        "absent_from_file": missing,
        "encoding": encoding,
    }


def choose_undp_csv(docs_body, label):
    """The composite-indices CSV link with the newest release year, from the UNDP page."""
    html = docs_body.decode("utf-8", errors="replace")
    hrefs = re.findall(r"""href=["']([^"']+\.csv)["']""", html, flags=re.IGNORECASE)
    candidates = []
    for href in hrefs:
        low = href.lower()
        if "composite" in low and "time" in low and "series" in low:
            url = urljoin(UNDP_DOCS_URL, href)
            years = [int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", url)]
            if years:
                candidates.append((max(years), url))
    if not candidates:
        raise SnapshotError(f"{label}: no composite-indices time-series CSV is linked from the UNDP page")
    candidates = sorted(set(candidates), key=lambda c: (-c[0], c[1]))
    best_year = candidates[0][0]
    best = [url for year, url in candidates if year == best_year]
    if len(best) != 1:
        raise SnapshotError(f"{label}: {len(best)} CSVs claim release {best_year}; refusing to choose")
    return best[0], best_year, [url for _, url in candidates]


def latest_per_country(table_by_iso3):
    """{iso3: {'year': ..., 'value': ...} or None} for every requested country.

    The newest non-null year. A country the series does not mention at all is None,
    the same as one whose every year is null, so the manifest lists every country.
    """
    latest = {}
    for iso3 in MAP_ISO3:
        present = [(year, value) for year, value in table_by_iso3.get(iso3, {}).items() if value is not None]
        if present:
            year, value = max(present)
            latest[iso3] = {"year": year, "value": value}
        else:
            latest[iso3] = None
    return latest


# ---------------------------------------------------------------------------
# Network. Every request goes through _get: one per second, the retry policy.
# Tests replace _get, so no test reaches a socket.
# ---------------------------------------------------------------------------

_last_start = [None]


def _pace():
    now = _clock()
    if _last_start[0] is not None:
        wait = MIN_INTERVAL_S - (now - _last_start[0])
        if wait > 0:
            _sleep(wait)
            now = _clock()
    _last_start[0] = now


def _get(label, url, params=None):
    """Return (body, full_url) for a 200 response, retrying as the module describes."""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        _pace()
        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_S)
        except requests.RequestException as exc:
            reason = f"{type(exc).__name__}: {exc}"
        else:
            if response.status_code == 200:
                return bytes(response.content), response.url
            if response.status_code not in RETRYABLE_STATUS:
                raise SnapshotError(f"{label}: HTTP {response.status_code}; not retried")
            reason = f"HTTP {response.status_code}"
        if attempt == RETRY_ATTEMPTS:
            raise SnapshotError(f"{label}: {reason} on all {RETRY_ATTEMPTS} attempts; giving up")
        wait = BACKOFF_BASE_S * 2 ** (attempt - 1)
        print(f"{label}: {reason}; attempt {attempt} of {RETRY_ATTEMPTS} failed, retrying in {wait} s",
              file=sys.stderr)
        _sleep(wait)


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

def _wb_series_url(code):
    return f"{WB_BASE}/country/{';'.join(MAP_ISO3)}/indicator/{code}"


def _wb_metadata(label, code):
    """The indicator's own record: its name and the source it is published under."""
    body, _ = _get(f"{label} metadata", f"{WB_BASE}/indicator/{code}", {"format": "json"})
    payload = _load_json(body, f"{label} metadata")
    if isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], dict) and "message" in payload[0]:
        raise SnapshotError(f"{label} metadata: the World Bank does not know {code!r}: {payload[0]['message']!r}")
    if not (isinstance(payload, list) and len(payload) == 2 and isinstance(payload[1], list) and payload[1]):
        raise SnapshotError(f"{label} metadata: no indicator record for {code!r}")
    record = payload[1][0]
    source = record.get("source") or {}
    return {
        "id": record.get("id"),
        "name": record.get("name"),
        "source_id": source.get("id"),
        "source_name": source.get("value"),
    }


def _fetch_wb(name, code, spec, requests_log, snapshot_year=None):
    """Every page of one World Bank series for MAP_ISO3, raw and checked."""
    label = f"{name} ({code})"
    metadata = _wb_metadata(label, code)
    if metadata["id"] != code:
        raise SnapshotError(f"{label}: the metadata answered for {metadata['id']!r}")
    if spec.get("expected_source") and spec["expected_source"] not in (metadata["source_name"] or ""):
        raise SnapshotError(
            f"{label}: published under {metadata['source_name']!r}, not a source containing "
            f"{spec['expected_source']!r}; refusing"
        )
    pages = []
    page_number = 1
    while True:
        params = {"format": "json", "per_page": WB_PER_PAGE, "page": page_number}
        page_label = f"{label} page {page_number}"
        fetched_at = _utc_now()
        body, full_url = _get(page_label, _wb_series_url(code), params)
        parsed = parse_wb_page(body, page_label, code, spec, set(MAP_ISO3), snapshot_year)
        requests_log.append({
            "page": page_number,
            "url": full_url,
            "http_status": 200,
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "fetched_at": fetched_at,
            "rows": len(parsed["rows"]),
            "api_total": parsed["total"],
            "api_pages": parsed["pages"],
            "lastupdated": parsed["lastupdated"],
        })
        pages.append((page_number, body, parsed))
        if parsed["pages"] <= page_number:
            break
        page_number += 1
    rows = assemble_wb_series([p for _, _, p in pages], label)
    return {
        "metadata": metadata,
        "pages": pages,
        "rows": rows,
        "indicator_name": pages[0][2]["indicator_name"],
        "lastupdated": pages[0][2]["lastupdated"],
    }


def _fetch_hdi(snapshot_year=None):
    """The UNDP docs page (kept raw), the newest composite-indices CSV on it, and that CSV."""
    label = "hdi"
    docs_fetched_at = _utc_now()
    docs_body, docs_url = _get(f"{label} documentation page", UNDP_DOCS_URL, None)
    csv_url, release_year, candidates = choose_undp_csv(docs_body, label)
    csv_fetched_at = _utc_now()
    csv_body, csv_full = _get(f"{label} csv", csv_url, None)
    if _looks_like_html(csv_body):
        raise SnapshotError(f"{label} csv: the response is HTML, not CSV")
    table = parse_undp_csv(csv_body, f"{label} csv", set(MAP_ISO3), snapshot_year)
    return {
        "docs": {
            "url": docs_url,
            "bytes": len(docs_body),
            "sha256": hashlib.sha256(docs_body).hexdigest(),
            "fetched_at": docs_fetched_at,
            "body": docs_body,
        },
        "csv": {
            "url": csv_full,
            "bytes": len(csv_body),
            "sha256": hashlib.sha256(csv_body).hexdigest(),
            "fetched_at": csv_fetched_at,
            "body": csv_body,
        },
        "release_year": release_year,
        "candidates": candidates,
        "table": table["table"],
        "years": table["years"],
        "absent_from_file": table["absent_from_file"],
        "encoding": table["encoding"],
    }


def _check_no_empty_series(name, table_by_iso3):
    if not any(v is not None for by_year in table_by_iso3.values() for v in by_year.values()):
        raise SnapshotError(f"{name}: not one country has a value; refusing a snapshot with no data")


def build_plan(renewables, only):
    """The series to fetch, in order, as (name, kind, code, spec)."""
    code = RENEWABLE_CODES[renewables]
    renew_spec = {
        "label": f"Renewable share ({code})",
        "unit": "% (" + ("of total final energy consumption" if renewables == "final"
                         else "of total electricity output") + ")",
        "range": (0.0, 100.0),
        "name_must_contain": RENEWABLE_NAME_TOKEN,
        "expected_source": WDI_SOURCE,
    }
    plan = [
        ("gini", "world_bank", GINI_CODE, {
            "label": "Gini index (World Bank SI.POV.GINI)",
            "unit": WB_SPECS["gini"]["unit"],
            "range": (0.0, 100.0),
            "name_must_contain": "Gini",
            "expected_source": WDI_SOURCE,
        }),
        ("renewables", "world_bank", code, renew_spec),
        ("co2_per_capita", "world_bank", CO2_CODE, WB_SPECS["co2_per_capita"]),
        ("gov_effectiveness", "world_bank", GOV_WGI_CODE, WB_SPECS["gov_effectiveness"]),
        ("hdi", "undp_csv", None, None),
    ]
    return [entry for entry in plan if entry[0] in only]


def _series_summary(name, series):
    rows = series["rows"]
    table = {}
    for row in rows:
        table.setdefault(row["iso3"], {})[row["year"]] = row["value"]
    _check_no_empty_series(name, table)
    latest = latest_per_country(table)
    return {
        "rows": len(rows),
        "values": sum(1 for r in rows if r["value"] is not None),
        "countries_with_value": sum(1 for v in latest.values() if v is not None),
        "countries_requested": len(MAP_ISO3),
        "latest_year_per_country": {iso3: (latest[iso3] and latest[iso3]["year"]) for iso3 in MAP_ISO3},
        "latest_per_country": latest,
    }


def take_snapshot(root, date_str, renewables=DEFAULT_RENEWABLES, only=None):
    """Fetch the sources into <root>/<date_str>; return the manifest.

    Raises SnapshotError, leaving <date_str>.partial in place, if anything is wrong.
    Nothing that exists is ever opened for writing.
    """
    if renewables not in RENEWABLE_CODES:
        raise SnapshotError(f"renewables must be one of {sorted(RENEWABLE_CODES)}, not {renewables!r}")
    only = list(only or ["gini", "renewables", "co2_per_capita", "gov_effectiveness", "hdi"])
    root = Path(root)
    snapshot_year = int(date_str[:4])
    out = root / date_str
    partial = root / f"{date_str}.partial"
    _refuse_existing(out, partial)

    started = _utc_now()
    created = _missing_dirs(root)
    root.mkdir(parents=True, exist_ok=True)
    for new_dir in created:
        _flush_dir(new_dir.parent)
    partial.mkdir()
    files = {}
    try:
        indicators = {}
        written = []

        def write(rel, body):
            _write_new(partial / rel, body)
            written.append(rel)
            files[rel] = {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}

        plan = build_plan(renewables, only)
        for name, kind, code, spec in plan:
            if kind == "world_bank":
                log = []
                series = _fetch_wb(name, code, spec, log, snapshot_year)
                for page_number, body, _parsed in series["pages"]:
                    write(f"raw/{name}/page-{page_number:05d}.json", body)
                summary = _series_summary(name, series)
                indicators[name] = {
                    "kind": "world_bank",
                    "code": code,
                    "label": spec["label"],
                    "unit": spec["unit"],
                    "range": list(spec["range"]),
                    "metadata": series["metadata"],
                    "lastupdated": series["lastupdated"],
                    "requests": log,
                    **summary,
                }
            else:
                hdi = _fetch_hdi(snapshot_year)
                write("raw/undp-documentation-and-downloads.html", hdi["docs"]["body"])
                write("raw/hdi/composite_indices_time_series.csv", hdi["csv"]["body"])
                _check_no_empty_series("hdi", hdi["table"])
                latest = latest_per_country(hdi["table"])
                indicators[name] = {
                    "kind": "undp_csv",
                    "label": "UNDP Human Development Index (composite indices time series)",
                    "unit": "index, 0-1",
                    "range": list(HDI_RANGE),
                    "docs_url": hdi["docs"]["url"],
                    "docs_sha256": hdi["docs"]["sha256"],
                    "docs_fetched_at": hdi["docs"]["fetched_at"],
                    "candidates": hdi["candidates"],
                    "csv_url": hdi["csv"]["url"],
                    "csv_sha256": hdi["csv"]["sha256"],
                    "csv_fetched_at": hdi["csv"]["fetched_at"],
                    "release": f"UNDP Human Development Report, release year {hdi['release_year']}",
                    "years_in_file": hdi["years"],
                    "encoding": hdi["encoding"],
                    "absent_from_file": hdi["absent_from_file"],
                    "rows": len(hdi["table"]),
                    "countries_with_value": sum(1 for v in latest.values() if v is not None),
                    "countries_requested": len(MAP_ISO3),
                    "latest_year_per_country": {iso3: (latest[iso3] and latest[iso3]["year"]) for iso3 in MAP_ISO3},
                    "latest_per_country": latest,
                }

        files_listing = sorted(
            [{"path": rel, **meta} for rel, meta in files.items()], key=lambda f: f["path"]
        )
        manifest = {
            "tool": _tool_info(),
            "snapshot_date_utc": date_str,
            "started_utc": started,
            "finished_utc": _utc_now(),
            "countries_iso3": list(MAP_ISO3),
            "renewables_choice": renewables,
            "renewables_code": RENEWABLE_CODES[renewables],
            "gov_effectiveness_code": GOV_WGI_CODE,
            "co2_code": CO2_CODE,
            "indicators": indicators,
            "files": files_listing,
            "snapshot_digest": _snapshot_digest(files_listing),
            "snapshot_digest_method": SNAPSHOT_DIGEST_METHOD,
        }
        manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
        _write_new(partial / "manifest.json", manifest_bytes)
        _flush_dir(partial / "raw")
        for rel in written:
            _flush_dir((partial / rel).parent)
        _flush_dir(partial)
        os.rename(partial, out)
    except BaseException:
        print(f"snapshot did not finish; {partial} is left in place for inspection", file=sys.stderr)
        raise
    try:
        _fsync_dir(out.parent)
    except OSError as exc:
        raise SnapshotError(
            f"{out} was written and renamed into place, but fsync of {out.parent} failed ({exc}). "
            "The snapshot directory exists; its durability is unconfirmed. Nothing was deleted."
        ) from exc
    return manifest


def _snapshot_digest(files_listing):
    lines = sorted(f"{f['sha256']}  {f['path']}" for f in files_listing)
    return hashlib.sha256("".join(line + "\n" for line in lines).encode("utf-8")).hexdigest()


def _write_new(path, body):
    """Create a file that must not already exist, and flush it to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as fh:
        fh.write(body)
        fh.flush()
        os.fsync(fh.fileno())


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _flush_dir(path):
    try:
        _fsync_dir(path)
    except OSError as exc:
        raise SnapshotError(f"could not flush {path} to disk ({exc}); the snapshot is not durable") from exc


def _missing_dirs(path):
    """The directories at path and above it that do not exist yet, deepest first."""
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    return missing


def _refuse_existing(out, partial):
    if out.exists():
        raise SnapshotError(
            f"refusing to write: {out} already exists. A snapshot is immutable; use a new date."
        )
    if partial.exists():
        raise SnapshotError(
            f"refusing to write: {partial} was left by an earlier run that did not finish. "
            "Inspect it, then remove it by hand."
        )


def probe(renewables):
    """Read the candidate codes' metadata, the first page of each series, and the UNDP links. Writes nothing."""
    print("World Bank indicator metadata (WGI candidates):")
    for code in GOV_WGI_CANDIDATES:
        try:
            print(f"  {code}: {_wb_metadata('probe', code)}")
        except SnapshotError as exc:
            print(f"  {code}: {exc}")
    print("World Bank indicator metadata (chosen series):")
    for code in (GINI_CODE, RENEWABLE_CODES["final"], RENEWABLE_CODES["electricity"], CO2_CODE):
        try:
            print(f"  {code}: {_wb_metadata('probe', code)}")
        except SnapshotError as exc:
            print(f"  {code}: {exc}")
    print("World Bank first page per series (countries requested: 33):")
    for code in (GINI_CODE, RENEWABLE_CODES[renewables], CO2_CODE, GOV_WGI_CODE):
        try:
            body, _ = _get(f"probe {code}", _wb_series_url(code), {"format": "json", "per_page": WB_PER_PAGE, "page": 1})
            payload = _load_json(body, f"probe {code}")
            if isinstance(payload, list) and payload and isinstance(payload[0], dict):
                meta = payload[0]
                rows = payload[1] if len(payload) > 1 and payload[1] else []
                print(f"  {code}: meta.total={meta.get('total')} pages={meta.get('pages')} "
                      f"lastupdated={meta.get('lastupdated')} rows_on_page={len(rows)}")
            else:
                print(f"  {code}: unexpected body: {str(payload)[:200]}")
        except SnapshotError as exc:
            print(f"  {code}: {exc}")
    print("UNDP documentation page:")
    body, _ = _get("probe undp", UNDP_DOCS_URL, None)
    try:
        url, year, candidates = choose_undp_csv(body, "probe undp")
        print(f"  chosen: {url} (release year {year})")
        for candidate in candidates:
            print(f"  candidate: {candidate}")
    except SnapshotError as exc:
        print(f"  {exc}")
    print("dry run: nothing written")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Snapshot the public sources behind the map's country inputs, as raw bytes."
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                        help="directory that holds the dated snapshots (default: %(default)s)")
    parser.add_argument("--renewables", choices=sorted(RENEWABLE_CODES), default=DEFAULT_RENEWABLES,
                        help="which renewables indicator this snapshot holds; exactly one")
    parser.add_argument("--only", nargs="+",
                        choices=["gini", "renewables", "co2_per_capita", "gov_effectiveness", "hdi"],
                        help="snapshot only these inputs (for a coverage check; not the map's snapshot)")
    parser.add_argument("--probe", action="store_true",
                        help="read the candidate codes and the UNDP link; write nothing")
    args = parser.parse_args(argv)
    try:
        if args.probe:
            probe(args.renewables)
            return 0
        manifest = take_snapshot(args.root, _utc_date(), args.renewables, args.only)
    except SnapshotError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    out = Path(args.root) / manifest["snapshot_date_utc"]
    manifest_sha = hashlib.sha256((out / "manifest.json").read_bytes()).hexdigest()
    print(f"snapshot written: {out}")
    print(f"snapshot digest: {manifest['snapshot_digest']}")
    print(f"manifest sha256: {manifest_sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
