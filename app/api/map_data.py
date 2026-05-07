from fastapi import APIRouter
from typing import List, Dict, Any
import math

router = APIRouter(tags=["map"])

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
]

def _band(esg: int) -> str:
    if esg >= 80: return "leader"
    if esg >= 70: return "advanced"
    if esg >= 60: return "developing"
    if esg >= 50: return "emerging"
    return "lagging"

@router.get("/map/countries")
def map_countries():
    items = []
    for c in COUNTRIES:
        items.append({
            **c,
            "band": _band(c["esg"]),
            "co2_intensity_t_per_capita": round(20.0 - c["esg"] * 0.18, 2),
            "renewable_share_pct": min(round(c["esg"] * 0.95, 1), 95.0),
        })
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
        "countries": items,
    }

@router.get("/map/countries/{code}")
def country_detail(code: str):
    code = code.upper()
    for c in COUNTRIES:
        if c["code"] == code:
            return {
                **c,
                "band": _band(c["esg"]),
                "co2_intensity_t_per_capita": round(20.0 - c["esg"] * 0.18, 2),
                "renewable_share_pct": min(round(c["esg"] * 0.95, 1), 95.0),
                "csrd_alignment": round(c["esg"] * 0.92, 1),
                "audit_ready_share_pct": round(max(0.0, c["esg"] - 30) * 1.4, 1),
            }
    return {"error": f"Country {code} not found"}, 404
