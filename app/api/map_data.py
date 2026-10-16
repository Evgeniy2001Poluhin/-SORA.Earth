from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any, Tuple
import math

router = APIRouter(tags=["map"])

# What `esg_source` says about where a country's score came from. Served as
# `esg_source_meanings` by /map/countries, so a client can read the tokens
# without this file.
ESG_SOURCE_LIVE = "world_bank_live"      # fresh live-cache entry
ESG_SOURCE_STATIC = "static_benchmark"   # cache empty or stale: BENCHMARKS
ESG_SOURCE_HARDCODED = "hardcoded"       # no external data: COUNTRIES profile
ESG_SOURCE_MEANINGS = {
    ESG_SOURCE_LIVE: "Score computed from a fresh live-cache entry (under 24h old). "
                     "Its hdi, co2 and gov_effectiveness inputs come from BENCHMARKS.",
    ESG_SOURCE_STATIC: "Live cache empty or stale. Score computed from the static BENCHMARKS row "
                       "in app/country_benchmarks.py.",
    ESG_SOURCE_HARDCODED: "No external data for this country. Score is the hand-entered COUNTRIES "
                          "profile in app/api/map_data.py.",
}

# The source tags app/external_data.py writes per indicator that are measurements.
# "benchmark" and "global_avg" are static figures, not measurements.
LIVE_INDICATOR_SOURCES = frozenset({"world_bank", "oecd"})


def _unmeasured() -> Dict[str, Any]:
    """Physical fields when no live measurement exists: null, never derived."""
    return {"co2_intensity_t_per_capita": None, "renewable_share_pct": None}

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

def _band(esg: int) -> str:
    if esg >= 80: return "leader"
    if esg >= 70: return "advanced"
    if esg >= 60: return "developing"
    if esg >= 50: return "emerging"
    return "lagging"


_NAME_MAP={"United States":"USA","United Kingdom":"UK","South Korea":"Korea","Czech Republic":"Czechia"}
def _measured(ind: Dict[str, Any], sources: Dict[str, str], key: str):
    """The value of one indicator, only if the live cache says a live source published it."""
    if sources.get(key) in LIVE_INDICATOR_SOURCES:
        return ind.get(key)
    return None


def _live_esg(name: str, fallback: int) -> Tuple[int, Dict[str, Any], str]:
    """Score one country. Returns (score, extra, esg_source).

    esg_source says where the score's inputs came from:
      ESG_SOURCE_LIVE       the live cache held a fresh entry for the country
      ESG_SOURCE_STATIC     the cache was empty or stale: BENCHMARKS supplied the inputs
      ESG_SOURCE_HARDCODED  no external data: the COUNTRIES profile score is returned

    `extra` always carries renewable_share_pct and co2_intensity_t_per_capita. Each
    is the measured value when a live source published it, otherwise None. Neither
    is ever derived from the score.
    """
    name=_NAME_MAP.get(name,name)
    try:
        from app.external_data import get_merged_country_data
        d = get_merged_country_data(name)
        if not d:
            return fallback, _unmeasured(), ESG_SOURCE_HARDCODED
        live = d.get("live", {}) or {}
        ind = live.get("indicators", {}) or {}
        sources = live.get("indicator_sources", {}) or {}
        # Score inputs. Where the cache has no value these fall back to BENCHMARKS
        # and then to defaults, exactly as before; the reported physical fields below
        # do not use those fallbacks.
        co2 = ind.get("co2_per_capita", d.get("co2_per_capita", 5.0))
        renew = ind.get("renewable_share", d.get("renewable_share", 20.0))
        gini = ind.get("gini_index", d.get("gini_index", 35.0))
        # gov_effectiveness can be None (e.g., Finland); the fallback chain uses 0.0
        gov = ind.get("gov_effectiveness")
        if gov is None:
            gov = d.get("gov_effectiveness")
        if gov is None:
            gov = 0.0
        hdi = d.get("hdi", 0.7)
        e = 0.6*min(renew,100) + 0.4*max(0,(16-co2)/16*100)
        soc = 0.7*hdi*100 + 0.3*max(0,(60-gini)/40*100)
        g = (gov+2.5)/5*100
        total = round(0.4*e + 0.35*soc + 0.25*g)
        m_renew = _measured(ind, sources, "renewable_share")
        m_co2 = _measured(ind, sources, "co2_per_capita")
        extra = {
            "co2_intensity_t_per_capita": round(m_co2, 1) if m_co2 is not None else None,
            "renewable_share_pct": round(m_renew, 1) if m_renew is not None else None,
            "hdi": round(hdi,3),
            "gini_index": round(gini,1),
            "gov_effectiveness": round(gov,2),
            "esg_breakdown": {"E": round(e), "S": round(soc), "G": round(g)},
        }
        source = ESG_SOURCE_LIVE if live else ESG_SOURCE_STATIC
        return total, extra, source
    except Exception:
        return fallback, _unmeasured(), ESG_SOURCE_HARDCODED


@router.get("/map/countries")
def map_countries():
    """Every country's score, with `esg_source` naming where it came from.

    `esg_source` is one of the tokens in `esg_source_meanings`. The physical fields
    `renewable_share_pct` and `co2_intensity_t_per_capita` are measured values or
    null; nothing in this response is derived from the ESG score.
    """
    items = []
    for c in COUNTRIES:
        score, extra, src = _live_esg(c["name"], c["esg"])
        item = {**c, "esg": score, "esg_source": src, "band": _band(score)}
        item.update(extra)
        items.append(item)
    items.sort(key=lambda x: -x["esg"])
    return {
        "total_countries": len(items),
        "bands": {
            "leader": "ESG >= 80",
            "advanced": "70-79",
            "developing": "60-69",
            "emerging": "50-59",
            "lagging": "< 50",
        },
        "esg_source_meanings": ESG_SOURCE_MEANINGS,
        "countries": items,
    }

@router.get("/map/countries/{code}")
def country_detail(code: str):
    """One country's reference profile.

    Its `esg` is the hand-entered COUNTRIES score (esg_source "hardcoded"), which can
    differ from the score /map/countries computes for the same country from live or
    benchmark data. The physical fields and the two ESG-derived fields are null:
    nothing measures them, and they were formulas of the score.
    """
    code = code.upper()
    for c in COUNTRIES:
        if c["code"] == code:
            return {
                **c,
                "esg_source": ESG_SOURCE_HARDCODED,
                "band": _band(c["esg"]),
                **_unmeasured(),
                "csrd_alignment": None,
                "audit_ready_share_pct": None,
            }
    raise HTTPException(status_code=404, detail=f"Country {code} not found")
