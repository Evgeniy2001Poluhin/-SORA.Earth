"""The pinned official inputs the map's ESG score is computed from (Council decision C8, part 2).

data/map_inputs/<snapshot date>/map_inputs.json is built offline by
scripts/build_map_inputs.py from a verified snapshot of the official sources.
manifest.json next to it records the table's byte size and sha256. This module
reads the table only when those bytes match the manifest.

A table that is missing, unreadable, has a different hash, has another schema,
or fails the table-level scale checks gives no inputs at all. Nothing falls back
to BENCHMARKS, to the COUNTRIES profile or to the live cache: when the table is
unavailable, every country is incomplete.

Age is measured at the date of use, not at the snapshot's date. A value whose age
at that date exceeds its maximum is missing, with the reason written out.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TABLE_DIR: Path = Path(__file__).resolve().parents[1] / "data" / "map_inputs" / "2026-10-18"
TABLE_NAME = "map_inputs.json"
MANIFEST_NAME = "manifest.json"
TABLE_SCHEMA = "map-inputs/v1"

# The five inputs the formula reads, in the order the table holds them.
INPUT_KEYS: Tuple[str, ...] = (
    "renewable_share",
    "co2_per_capita",
    "gini_index",
    "gov_effectiveness",
    "hdi",
)

# Maximum age, in years at the date of use, per input. These are part 1's policy
# (scripts/build_map_inputs.py, MAX_AGE_YEARS). load_table refuses a table whose own
# policy block says otherwise.
#
# gini_index: the 10-year window is a PROVISIONAL USAGE POLICY, not proven validity.
# It was chosen from the age distribution of the 2026-10-18 snapshot (ages 2 to 8
# years, one country with no figure), so that no country with a Gini on record is
# dropped. Nothing in the data shows a 10-year-old Gini is still the current measure.
# Revisit it when a second snapshot exists.
#
# EXPIRY OF THE SHIPPED TABLE. The 2026-10-18 table scores all 32 countries through
# 31 December 2026. On 1 January 2027 its renewables figures (2021, age 6 against a
# limit of 5) and its HDI figures (2023, age 4 against a limit of 3) are too old for
# every country, and the map is incomplete for all 33 until a newer snapshot is pinned
# under data/map_inputs. This is the policy working, not a fault: a stale score is
# refused rather than shown. Pinning the next table is the fix, not relaxing a limit.
MAX_AGE_YEARS: Dict[str, int] = {
    "renewable_share": 5,
    "co2_per_capita": 5,
    "gini_index": 10,
    "gov_effectiveness": 3,
    "hdi": 3,
}

# The scale each input's formula assumes. A value outside its scale is missing,
# never clipped or rescaled: a percentile or a 0-1 fraction in place of a WGI
# estimate would otherwise score as a plausible number.
SCALE: Dict[str, Tuple[float, float]] = {
    "renewable_share": (0.0, 100.0),    # percent of total final energy consumption
    "co2_per_capita": (0.0, 100.0),     # t CO2 per capita
    "gini_index": (0.0, 100.0),         # index, 0 to 100
    "gov_effectiveness": (-2.5, 2.5),   # WGI estimate, approximately -2.5 to +2.5
    "hdi": (0.0, 1.0),                  # index, 0 to 1
}

_YEAR = re.compile(r"^\d{4}$")


@dataclass(frozen=True)
class PinnedTable:
    """A table whose bytes matched its manifest. `rows` maps ISO3 to the row as written."""

    rows: Dict[str, Dict[str, Any]]
    table_sha256: str
    manifest_sha256: str
    snapshot_date: Optional[str]


@dataclass(frozen=True)
class TableState:
    """The table, or the reason there is none. Exactly one of the two is set."""

    table: Optional[PinnedTable]
    reason: Optional[str]


def _finite(value: Any) -> Optional[float]:
    """The value as a float when it is a finite real number; None for anything else.

    Booleans are refused: True is an int in Python and would otherwise score as 1.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _unavailable(reason: str) -> TableState:
    logger.warning("map inputs: pinned table unavailable: %s", reason)
    return TableState(table=None, reason=reason)


def _table_level_scale_problem(rows: List[Dict[str, Any]]) -> Optional[str]:
    """Checks a single value cannot show, because it depends on the whole column.

    Governance must span zero: a column with no negative value is a percentile or
    a 0-1 fraction, not the WGI estimate the formula is written for. Renewables must
    have a value above 1: a column that never exceeds 1 is a fraction, not a percent.
    """
    columns: Dict[str, List[float]] = {key: [] for key in INPUT_KEYS}
    for row in rows:
        inputs = row.get("inputs") if isinstance(row.get("inputs"), dict) else {}
        for key in INPUT_KEYS:
            entry = inputs.get(key)
            value = _finite(entry.get("value")) if isinstance(entry, dict) else None
            if value is not None:
                columns[key].append(value)
    gov = columns["gov_effectiveness"]
    if gov and not (any(v < 0 for v in gov) and any(v > 0 for v in gov)):
        return "gov_effectiveness does not span zero: not the WGI estimate scale"
    renew = columns["renewable_share"]
    if renew and not any(v > 1 for v in renew):
        return "renewable_share never exceeds 1: a fraction, not a percent"
    return None


def load_table(directory: Optional[Path] = None) -> TableState:
    """Read and verify the pinned table. Never raises; a failure is a TableState with a reason."""
    directory = Path(TABLE_DIR if directory is None else directory)
    try:
        manifest_bytes = (directory / MANIFEST_NAME).read_bytes()
        table_bytes = (directory / TABLE_NAME).read_bytes()
    except OSError as exc:
        return _unavailable(f"table files not readable ({exc.__class__.__name__})")

    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        entry = manifest["outputs"][TABLE_NAME]
        recorded_sha = str(entry["sha256"])
        recorded_bytes = int(entry["bytes"])
    except (ValueError, KeyError, TypeError):
        return _unavailable("manifest.json does not record the table")

    table_sha = hashlib.sha256(table_bytes).hexdigest()
    if len(table_bytes) != recorded_bytes or not hmac.compare_digest(table_sha, recorded_sha):
        return _unavailable("map_inputs.json does not match the sha256 in its manifest")

    try:
        doc = json.loads(table_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _unavailable("map_inputs.json is not JSON")
    if not isinstance(doc, dict) or doc.get("schema") != TABLE_SCHEMA:
        return _unavailable(f"map_inputs.json is not schema {TABLE_SCHEMA}")

    policy = doc.get("policy") if isinstance(doc.get("policy"), dict) else {}
    if policy.get("max_age_years") != MAX_AGE_YEARS:
        return _unavailable("the table's maximum ages differ from the code's")

    rows_list = doc.get("countries")
    if not isinstance(rows_list, list):
        return _unavailable("map_inputs.json has no countries list")
    rows: Dict[str, Dict[str, Any]] = {}
    for row in rows_list:
        iso3 = row.get("iso3") if isinstance(row, dict) else None
        if not isinstance(iso3, str) or not iso3:
            return _unavailable("a country row has no iso3")
        if iso3 in rows:
            return _unavailable(f"{iso3} appears twice in the table")
        rows[iso3] = row

    problem = _table_level_scale_problem(rows_list)
    if problem is not None:
        return _unavailable(problem)

    snapshot = doc.get("snapshot") if isinstance(doc.get("snapshot"), dict) else {}
    return TableState(
        table=PinnedTable(
            rows=rows,
            table_sha256=table_sha,
            manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
            snapshot_date=snapshot.get("date_utc"),
        ),
        reason=None,
    )


_STATE: Dict[str, TableState] = {}


def current_table_state() -> TableState:
    """The table this process serves, verified once and then kept. Lazy: nothing loads at import."""
    if "state" not in _STATE:
        _STATE["state"] = load_table()
    return _STATE["state"]


def reset_table_cache() -> None:
    """Forget the loaded table. For tests that point TABLE_DIR elsewhere."""
    _STATE.clear()


def use_date() -> date:
    """The date ages are measured at: today, in UTC. Tests replace this to freeze the clock."""
    return datetime.now(timezone.utc).date()


def resolve(table: PinnedTable, iso3: str, key: str, as_of: date) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """One input for one country: (provenance, None) when it qualifies, (None, reason) when not.

    A measured 0 is a value, not a missing one. Nothing is substituted for a missing
    value: not another year, not another indicator, not a default.
    """
    row = table.rows.get(iso3)
    if row is None:
        return None, "no row for this country in the pinned table"
    entry = (row.get("inputs") or {}).get(key)
    if not isinstance(entry, dict):
        return None, "no entry for this input in the pinned table"

    value = _finite(entry.get("value"))
    if value is None:
        if entry.get("value") is None:
            return None, "no observation in the pinned table"
        return None, "pinned value is not a finite number"

    low, high = SCALE[key]
    if not low <= value <= high:
        return None, f"value {value:g} is outside the input's scale {low:g} to {high:g}"

    year_text = entry.get("year")
    if not isinstance(year_text, str) or not _YEAR.match(year_text):
        return None, "the pinned value has no year"
    year = int(year_text)
    age = as_of.year - year
    if age < 0:
        return None, f"year {year} is after the date of use {as_of.isoformat()}"
    limit = MAX_AGE_YEARS[key]
    if age > limit:
        return None, f"too old: {age} years at {as_of.isoformat()}, the limit is {limit}"

    source = entry.get("source")
    release = entry.get("release")
    if not source or not release:
        return None, "the pinned value states no source or release"

    return {
        "value": value,
        "unit": entry.get("unit"),
        "year": year_text,
        "age_years": age,
        "source": source,
        "release": release,
        "fetched_at": entry.get("fetched_at"),
    }, None
