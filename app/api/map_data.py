"""Map ESG scores: one score per country, computed from pinned official inputs.

Contract (Council decision C8, part 2):

- The formula reads five inputs: renewable_share, co2_per_capita, gini_index,
  gov_effectiveness and hdi. They come only from the pinned table in
  data/map_inputs (app/map_inputs.py), verified against the manifest sha256 pinned
  in code.
- An input qualifies when the table holds a value for it, the value is finite and
  inside its scale, the source and release are stated, and its age at the date of
  use is within the maximum for that input. Anything else is missing, with a
  machine-readable reason (`input_reasons`: no_observation, too_old, ...).
- esg_status is "ok" when all five qualify and "incomplete" otherwise. An
  incomplete country has esg null and names its missing_inputs. Coverage never
  decides admissibility: no rule is relaxed to raise the count.
- Every qualifying input carries its max age and `expires_at`, the first day it is
  too old. The response carries the snapshot date and the earliest expiry, and says
  when that expiry is within EXPIRY_NOTICE_DAYS.
- The live cache does not feed the score. BENCHMARKS and the COUNTRIES profile do
  not feed it either. The COUNTRIES profile is returned as reference_profile only.
- A missing or unverifiable table means every country is incomplete. No value from
  any other source is substituted.
- /map/countries and /map/countries/{code} both build each country through
  _country_item, so the same code gives the same score, status, inputs and version.
"""
from fastapi import APIRouter, HTTPException
from typing import Any, Dict, List, Optional, Tuple

from app import map_inputs

router = APIRouter(tags=["map"])

METHODOLOGY_VERSION = "map-esg-v3"

# The inputs the formula reads. A score exists only when every one of them qualifies.
REQUIRED_INPUTS: Tuple[str, ...] = map_inputs.INPUT_KEYS

SCORE_OK = "ok"
SCORE_INCOMPLETE = "incomplete"
ESG_STATUS_MEANINGS = {
    SCORE_OK: "All five inputs are in the pinned official table (data/map_inputs), each within its "
              "maximum age at the date of use.",
    SCORE_INCOMPLETE: "At least one input is missing, outside its scale, too old at the date of use, "
                      "or unsourced. No score is given; missing_inputs names them, input_reasons "
                      "gives a code for each, and input_notes gives the sentence.",
}

# What `esg_source` says about where a score's inputs came from. Null when there is no score.
ESG_SOURCE_PINNED = "pinned_table"
ESG_SOURCE_MEANINGS = {
    ESG_SOURCE_PINNED: "Every input is read from the pinned official table, data/map_inputs. Each input "
                       "carries its source, release, year, age, maximum age and expires_at.",
}

REFERENCE_NOTE = ("Hand-entered COUNTRIES profile. Not a measurement, and not used to compute "
                  "esg, esg_status or any input.")

# 33 country profiles (lat, lon, base ESG profile)
# Coords are country centroids. The esg value is a hand-entered reference only: it is
# returned as reference_profile and never produces a score.
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


def score_country(code: str, *, as_of=None, state: Optional[map_inputs.TableState] = None) -> Dict[str, Any]:
    """Score one country from the pinned table. The only scoring function.

    `code` is the ISO3 code, which is the table's key. `as_of` is the date ages are
    measured at (default: today). `state` is the loaded table or its failure
    (default: the process's table). Returns esg (int, or None when incomplete),
    esg_status, esg_source, the qualifying inputs with their provenance and expiry,
    missing_inputs with a reason for each, the pillar breakdown, the physical fields,
    the snapshot date, the maximum ages, and the versions and digests the score was
    computed from.
    """
    as_of = map_inputs.use_date() if as_of is None else as_of
    state = map_inputs.current_table_state() if state is None else state
    table = state.table
    iso3 = code.upper()

    inputs: Dict[str, Dict[str, Any]] = {}
    missing: List[str] = []
    notes: Dict[str, str] = {}
    reasons: Dict[str, Dict[str, Any]] = {}
    for key in REQUIRED_INPUTS:
        if table is None:
            qualified, reason = None, map_inputs.unavailable_reason(state.reason)
        else:
            qualified, reason = map_inputs.resolve(table, iso3, key, as_of)
        if qualified is None:
            missing.append(key)
            notes[key] = reason["text"]
            reasons[key] = reason
        else:
            inputs[key] = qualified

    esg: Optional[int] = None
    breakdown: Optional[Dict[str, int]] = None
    status = SCORE_INCOMPLETE
    source: Optional[str] = None
    if not missing:
        status = SCORE_OK
        source = ESG_SOURCE_PINNED
        esg, breakdown = _formula(inputs)

    def _input(key: str) -> Optional[float]:
        return inputs[key]["value"] if key in inputs else None

    # The earliest day any of this country's table values is too old, whether or not it
    # qualifies today: the date the country's data starts to age out.
    row = table.rows.get(iso3) if table is not None else None
    country_expiries = map_inputs.row_expiries(row) if row is not None else {}
    earliest = min(country_expiries.values()).isoformat() if country_expiries else None

    renew, co2 = _input("renewable_share"), _input("co2_per_capita")
    hdi, gini, gov = _input("hdi"), _input("gini_index"), _input("gov_effectiveness")
    return {
        "esg": esg,
        "esg_status": status,
        "esg_source": source,
        "missing_inputs": missing,
        "input_notes": notes,
        "input_reasons": reasons,
        "inputs": inputs,
        "esg_breakdown": breakdown,
        "hdi": round(hdi, 3) if hdi is not None else None,
        "gini_index": round(gini, 1) if gini is not None else None,
        "gov_effectiveness": round(gov, 2) if gov is not None else None,
        "renewable_share_pct": round(renew, 1) if renew is not None else None,
        "co2_intensity_t_per_capita": round(co2, 1) if co2 is not None else None,
        "age_reference_date": as_of.isoformat(),
        "snapshot_date": table.snapshot_date if table is not None else None,
        "max_age_years": dict(map_inputs.MAX_AGE_YEARS),
        "earliest_expiry": earliest,
        "methodology_version": METHODOLOGY_VERSION,
        "inputs_table_sha256": state.table.table_sha256 if table is not None else None,
        "inputs_manifest_sha256": state.table.manifest_sha256 if table is not None else None,
    }


def _country_item(c: Dict[str, Any], as_of=None) -> Dict[str, Any]:
    """One country as both routes serve it. The only place a country's response is built."""
    scored = score_country(c["code"], as_of=as_of)
    item = {k: v for k, v in c.items() if k != "esg"}
    item.update(scored)
    item["band"] = _band(scored["esg"])
    item["reference_profile"] = {"esg": c["esg"], "note": REFERENCE_NOTE}
    return item


@router.get("/map/countries")
def map_countries():
    """Every country's score, with its status, provenance, reasons and expiry.

    `esg` is null for an incomplete country: it is never 0. `esg_status` is ok or
    incomplete. `inputs` gives each qualifying input's value, unit, year, age, maximum
    age, expires_at, source, release and fetch time. `input_reasons` gives a code for
    each missing input. `snapshot_date`, `earliest_expiry`, `days_to_earliest_expiry`
    and `expiry_notice` say when the pinned data stops being usable. The digests name
    the exact table the scores came from (null when no table is available).
    """
    as_of = map_inputs.use_date()
    items = [_country_item(c, as_of) for c in COUNTRIES]
    items.sort(key=lambda x: (x["esg"] is None, -(x["esg"] or 0), x["name"]))
    counts = {SCORE_OK: 0, SCORE_INCOMPLETE: 0}
    for item in items:
        counts[item["esg_status"]] += 1
    state = map_inputs.current_table_state()
    table = state.table
    earliest = map_inputs.earliest_expiry(table) if table is not None else None
    days = map_inputs.days_until(earliest, as_of)
    return {
        "total_countries": len(items),
        "scored_countries": counts[SCORE_OK],
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
        "table_available": table is not None,
        "table_unavailable_reason": state.reason,
        "snapshot_date": table.snapshot_date if table is not None else None,
        "max_age_years": dict(map_inputs.MAX_AGE_YEARS),
        "earliest_expiry": earliest.isoformat() if earliest is not None else None,
        "days_to_earliest_expiry": days,
        "expiry_notice": map_inputs.expiry_notice(days),
        "expiry_notice_days": map_inputs.EXPIRY_NOTICE_DAYS,
        "inputs_table_sha256": table.table_sha256 if table is not None else None,
        "inputs_manifest_sha256": table.manifest_sha256 if table is not None else None,
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
