"""Map ESG scores: one score per country, computed from the inputs it actually used.

Contract (Council decision C8):

- Five inputs are required by the formula: renewable_share, co2_per_capita,
  gini_index, gov_effectiveness and hdi.
- An input qualifies when it is a measured live value (World Bank or OECD, in a
  fresh live-cache entry), or a static reference value from BENCHMARKS whose
  source and period are stated (SOURCES or SOURCE_EXCEPTIONS). Anything else is
  missing: absent, not a finite number, a default constant, or a benchmark with
  no stated source. No default is ever substituted for a missing input.
- esg_status is "ok" when all five qualify as measured, "mixed" when all five
  qualify and some are static references, and "incomplete" otherwise. An
  incomplete country has esg null and names its missing_inputs.
- The COUNTRIES profile is a hand-entered reference. It is returned as
  reference_profile and never produces a score.
- /map/countries and /map/countries/{code} both build each country through
  _country_item, so the same code gives the same score, status, inputs and version.
"""
from fastapi import APIRouter, HTTPException
from typing import Any, Dict, List, Optional, Tuple
import logging
import math

from app.country_benchmarks import SOURCES, SOURCE_EXCEPTIONS
from app.external_data import get_merged_country_data, live_entry_fetched_at

logger = logging.getLogger(__name__)

router = APIRouter(tags=["map"])

METHODOLOGY_VERSION = "map-esg-v2"

# The inputs the formula reads. A score exists only when every one of them qualifies.
REQUIRED_INPUTS: Tuple[str, ...] = (
    "renewable_share",
    "co2_per_capita",
    "gini_index",
    "gov_effectiveness",
    "hdi",
)

# The source tags app/external_data.py writes per indicator that are measurements.
# "benchmark" and "global_avg" are static figures or defaults, never measurements.
LIVE_INDICATOR_SOURCES = frozenset({"world_bank", "oecd"})

SCORE_OK = "ok"
SCORE_MIXED = "mixed"
SCORE_INCOMPLETE = "incomplete"
ESG_STATUS_MEANINGS = {
    SCORE_OK: "All five inputs are measured (World Bank or OECD) in a fresh live-cache entry.",
    SCORE_MIXED: "All five inputs are valid. Some are static reference values from BENCHMARKS, "
                 "each with a stated source and period. Same formula as ok.",
    SCORE_INCOMPLETE: "At least one input is missing, invalid, or has no stated source. "
                      "No score is given; missing_inputs names them.",
}

# What `esg_source` says about where a score's inputs came from. Null when there is no score.
ESG_SOURCE_LIVE = "world_bank_live"
ESG_SOURCE_MIXED = "mixed"
ESG_SOURCE_MEANINGS = {
    ESG_SOURCE_LIVE: "Every input is a measured value from a fresh live-cache entry (under 24h old).",
    ESG_SOURCE_MIXED: "Some inputs are static reference values from the BENCHMARKS table. Each "
                      "carries its stated source and period under inputs.",
}

REFERENCE_NOTE = ("Hand-entered COUNTRIES profile. Not a measurement, and not used to compute "
                  "esg, esg_status or any input.")

# Map name -> the name BENCHMARKS and the live cache use.
_NAME_MAP = {"United States": "USA", "United Kingdom": "UK", "South Korea": "Korea", "Czech Republic": "Czechia"}

# 32 country profiles (lat, lon, base ESG profile)
# Coords are country centroids; scores derived deterministically
COUNTRIES: List[Dict[str, Any]] = [
    {"code":"SWE","name":"Sweden","lat":60.1,"lon":18.6,"esg":89},
    {"code":"NOR","name":"Norway","lat":60.5,"lon":8.5,"esg":91},
    {"code":"FIN","name":"Finland","lat":61.9,"lon":25.7,"esg":87},
    {"code":"DNK","name":"Denmark","lat":56.3,"lon":9.5,"esg":88},
    {"code":"ISL","name":"Iceland","lat":64.9,"lon":-19.0,"esg":86},
    {"code":"DEU","name":"Germany","lat":51.2,"lon":10.5,"esg":78},
    {"code":"FRA","name":"France","lat":46.6,"lon":2.2,"esg":76},
    {"code":"GBR","name":"United Kingdom","lat":54.3,"lon":-2.4,"esg":74},
    {"code":"NLD","name":"Netherlands","lat":52.1,"lon":5.3,"esg":80},
    {"code":"CHE","name":"Switzerland","lat":46.8,"lon":8.2,"esg":85},
    {"code":"AUT","name":"Austria","lat":47.5,"lon":14.5,"esg":81},
    {"code":"BEL","name":"Belgium","lat":50.5,"lon":4.5,"esg":75},
    {"code":"ESP","name":"Spain","lat":40.4,"lon":-3.7,"esg":72},
    {"code":"ITA","name":"Italy","lat":41.9,"lon":12.6,"esg":70},
    {"code":"PRT","name":"Portugal","lat":39.4,"lon":-8.2,"esg":73},
    {"code":"POL","name":"Poland","lat":51.9,"lon":19.1,"esg":62},
    {"code":"CZE","name":"Czech Republic","lat":49.8,"lon":15.5,"esg":67},
    {"code":"USA","name":"United States","lat":37.1,"lon":-95.7,"esg":68},
    {"code":"CAN","name":"Canada","lat":56.1,"lon":-106.3,"esg":80},
    {"code":"MEX","name":"Mexico","lat":23.6,"lon":-102.6,"esg":58},
    {"code":"BRA","name":"Brazil","lat":-14.2,"lon":-51.9,"esg":60},
    {"code":"ARG","name":"Argentina","lat":-38.4,"lon":-63.6,"esg":56},
    {"code":"CHL","name":"Chile","lat":-35.7,"lon":-71.5,"esg":66},
    {"code":"JPN","name":"Japan","lat":36.2,"lon":138.3,"esg":77},
    {"code":"KOR","name":"South Korea","lat":35.9,"lon":127.8,"esg":71},
    {"code":"CHN","name":"China","lat":35.9,"lon":104.2,"esg":54},
    {"code":"IND","name":"India","lat":20.6,"lon":78.9,"esg":51},
    {"code":"AUS","name":"Australia","lat":-25.3,"lon":133.8,"esg":73},
    {"code":"NZL","name":"New Zealand","lat":-40.9,"lon":174.9,"esg":83},
    {"code":"ZAF","name":"South Africa","lat":-30.6,"lon":22.9,"esg":55},
    {"code":"KEN","name":"Kenya","lat":-0.0,"lon":37.9,"esg":52},
    {"code":"ARE","name":"UAE","lat":23.4,"lon":53.8,"esg":63},
    {"code":"RUS","name":"Russia","lat":55.75,"lon":37.62,"esg":58},
]


def _band(esg: Optional[float]) -> Optional[str]:
    if esg is None: return None
    if esg >= 80: return "leader"
    if esg >= 70: return "advanced"
    if esg >= 60: return "developing"
    if esg >= 50: return "emerging"
    return "lagging"


def _finite(value: Any) -> Optional[float]:
    """The value as a float when it is a finite real number; None for anything else.

    Booleans are refused: True is an int in Python and would otherwise score as 1.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _text(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def _static_provenance(country: str, key: str) -> Optional[Dict[str, Any]]:
    """The source and period stated for a BENCHMARKS value, or None when none is stated."""
    exception = SOURCE_EXCEPTIONS.get((country, key))
    if exception is not None:
        return {"source": exception["source"], "series": exception.get("series"),
                "period": _text(exception.get("year"))}
    record = SOURCES.get(key) or {}
    if not record.get("source"):
        return None
    period = record.get("year")
    if period is None:
        period = record.get("years")
    if period is None:
        return None
    return {"source": record["source"], "series": record.get("series"), "period": _text(period)}


def _resolve(country: str, key: str, d: Dict[str, Any], ind: Dict[str, Any],
             sources: Dict[str, str], periods: Dict[str, Any],
             fetched_at: Optional[str]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """One required input: (provenance, None) when it qualifies, (None, reason) when it does not."""
    if key in ind and sources.get(key) in LIVE_INDICATOR_SOURCES:
        value = _finite(ind[key])
        if value is None:
            return None, "live value is not a finite number"
        return {"value": value, "source": sources[key], "period": _text(periods.get(key)),
                "fetched_at": fetched_at, "kind": "measured"}, None

    raw = d.get(key)
    if raw is None:
        if key in ind:
            return None, f"live cache holds a '{sources.get(key)}' value, which is not a measurement"
        return None, "no measured value and no BENCHMARKS value"
    value = _finite(raw)
    if value is None:
        return None, "BENCHMARKS value is not a finite number"
    provenance = _static_provenance(country, key)
    if provenance is None:
        return None, "BENCHMARKS value has no stated source and period"
    return {"value": value, "source": provenance["source"], "period": provenance["period"],
            "fetched_at": None, "kind": "static"}, None


def _measured_value(ind: Dict[str, Any], sources: Dict[str, str], key: str) -> Optional[float]:
    """A live measurement for display, whatever the score's status. Never a static value."""
    if key in ind and sources.get(key) in LIVE_INDICATOR_SOURCES:
        return _finite(ind[key])
    return None


def _formula(inputs: Dict[str, Dict[str, Any]]) -> Tuple[int, Dict[str, int]]:
    renew = inputs["renewable_share"]["value"]
    co2 = inputs["co2_per_capita"]["value"]
    gini = inputs["gini_index"]["value"]
    gov = inputs["gov_effectiveness"]["value"]
    hdi = inputs["hdi"]["value"]
    e = 0.6 * min(renew, 100) + 0.4 * max(0, (16 - co2) / 16 * 100)
    soc = 0.7 * hdi * 100 + 0.3 * max(0, (60 - gini) / 40 * 100)
    g = (gov + 2.5) / 5 * 100
    total = round(0.4 * e + 0.35 * soc + 0.25 * g)
    return total, {"E": round(e), "S": round(soc), "G": round(g)}


def score_country(name: str) -> Dict[str, Any]:
    """Score one country from the inputs it actually has. The only scoring function.

    Returns esg (int, or None when incomplete), esg_status, esg_source, the inputs
    that qualified with their provenance, missing_inputs with a reason for each,
    the pillar breakdown, the measured physical fields, and methodology_version.
    """
    lookup = _NAME_MAP.get(name, name)
    failure: Optional[str] = None
    try:
        d = get_merged_country_data(lookup) or {}
        stamp = live_entry_fetched_at(lookup)
    except Exception:
        logger.exception("map score: country data lookup failed for %s", name)
        d, stamp, failure = {}, None, "country data lookup failed"

    live = d.get("live") or {}
    ind = live.get("indicators") or {}
    sources = live.get("indicator_sources") or {}
    periods = live.get("indicator_periods") or {}
    fetched_at = stamp.isoformat() if stamp is not None else None

    inputs: Dict[str, Dict[str, Any]] = {}
    missing: List[str] = []
    notes: Dict[str, str] = {}
    for key in REQUIRED_INPUTS:
        if failure is not None:
            qualified, reason = None, failure
        else:
            qualified, reason = _resolve(lookup, key, d, ind, sources, periods, fetched_at)
        if qualified is None:
            missing.append(key)
            notes[key] = reason or "missing"
        else:
            inputs[key] = qualified

    esg: Optional[int] = None
    breakdown: Optional[Dict[str, int]] = None
    status = SCORE_INCOMPLETE
    source: Optional[str] = None
    if not missing:
        kinds = {item["kind"] for item in inputs.values()}
        status = SCORE_OK if kinds == {"measured"} else SCORE_MIXED
        source = ESG_SOURCE_LIVE if status == SCORE_OK else ESG_SOURCE_MIXED
        esg, breakdown = _formula(inputs)

    def _input(key: str) -> Optional[float]:
        return inputs[key]["value"] if key in inputs else None

    renew_m = _measured_value(ind, sources, "renewable_share")
    co2_m = _measured_value(ind, sources, "co2_per_capita")
    hdi, gini, gov = _input("hdi"), _input("gini_index"), _input("gov_effectiveness")
    return {
        "esg": esg,
        "esg_status": status,
        "esg_source": source,
        "missing_inputs": missing,
        "input_notes": notes,
        "inputs": inputs,
        "esg_breakdown": breakdown,
        "hdi": round(hdi, 3) if hdi is not None else None,
        "gini_index": round(gini, 1) if gini is not None else None,
        "gov_effectiveness": round(gov, 2) if gov is not None else None,
        "renewable_share_pct": round(renew_m, 1) if renew_m is not None else None,
        "co2_intensity_t_per_capita": round(co2_m, 1) if co2_m is not None else None,
        "methodology_version": METHODOLOGY_VERSION,
    }


def _country_item(c: Dict[str, Any]) -> Dict[str, Any]:
    """One country as both routes serve it. The only place a country's response is built."""
    scored = score_country(c["name"])
    item = {k: v for k, v in c.items() if k != "esg"}
    item.update(scored)
    item["band"] = _band(scored["esg"])
    item["reference_profile"] = {"esg": c["esg"], "note": REFERENCE_NOTE}
    return item


@router.get("/map/countries")
def map_countries():
    """Every country's score, with its status, provenance and the inputs it is missing.

    `esg` is null for an incomplete country: it is never 0. `esg_status` is ok, mixed or
    incomplete. `inputs` gives each qualifying input's value, source, period and fetch
    time (null when unknown). Nothing in this response is a default or a derived value.
    """
    items = [_country_item(c) for c in COUNTRIES]
    items.sort(key=lambda x: (x["esg"] is None, -(x["esg"] or 0), x["name"]))
    counts = {SCORE_OK: 0, SCORE_MIXED: 0, SCORE_INCOMPLETE: 0}
    for item in items:
        counts[item["esg_status"]] += 1
    return {
        "total_countries": len(items),
        "scored_countries": counts[SCORE_OK] + counts[SCORE_MIXED],
        "status_counts": counts,
        "bands": {
            "leader": "ESG >= 80",
            "advanced": "70-79",
            "developing": "60-69",
            "emerging": "50-59",
            "lagging": "< 50",
        },
        "esg_status_meanings": ESG_STATUS_MEANINGS,
        "esg_source_meanings": ESG_SOURCE_MEANINGS,
        "methodology_version": METHODOLOGY_VERSION,
        "countries": items,
    }


@router.get("/map/countries/{code}")
def country_detail(code: str):
    """One country, built by the same function as /map/countries, so the two agree."""
    code = code.upper()
    for c in COUNTRIES:
        if c["code"] == code:
            return {**_country_item(c), "csrd_alignment": None, "audit_ready_share_pct": None}
    raise HTTPException(status_code=404, detail=f"Country {code} not found")
