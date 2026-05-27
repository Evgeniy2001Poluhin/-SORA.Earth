"""Co-Pilot API with RAG."""
from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import logging
from app.services.copilot import explain_prediction, health as copilot_health

router = APIRouter()
log = logging.getLogger(__name__)

class CopilotRequest(BaseModel):
    probability: float
    features: Dict[str, Any]
    shap_values: Optional[List[Dict[str, Any]]] = None
    project: Optional[Dict[str, Any]] = None
    model_version: Optional[str] = "v5"

def _build_rag_query(probability, features):
    parts = []
    co2 = features.get("co2_reduction")
    bud = features.get("budget")
    soc = features.get("social_impact")
    dur = features.get("duration_months")
    cpd = features.get("co2_(co2)} t/yr")
    if bud: parts.append(f"budget {int(bud)/1000000:.1f}M USD")
    if soc is not None: parts.append(f"social impact {int(soc)}")
    if dur: parts.append(f"{int(dur)} month duration")
    if cpd: parts.append(f"CO2 efficiency {cpd:.2f}")
    parts.append(f"ML probability {probability:.2f}")
    return ", ".join(parts)

def _retrieve_sources(query, k=4):
    try:
        from app.services.rag import get_retriever
        hits = get_retriever().search(query, k=k)
        out = []
        for h in hits:
            txt = h["text"]
            excerpt = txt[:240] + ("..." if len(txt) > 240 else "")
            out.append({"id": h["id"], "title": h["title"], "category": h["category"], "score": h["score"], "excerpt": excerpt})
        return out
    except Exception as e:
        log.warning("RAG failed: %s", e)
        return []

@router.post("/copilot/explain")
def explain(payload: CopilotRequest, k: int = Query(4, ge=1, le=8)):
    base = explain_prediction(probability=payload.probability, features=payload.features, shap_values=payload.shap_values, project=payload.project, model_version=payload.model_version or "v5")
    q = _build_rag_query(payload.probability, payload.features)
    src = _retrieve_sources(q, k=k)
    if isinstance(base, dict):
        base["sources"] = src
        base["rag_query"] = q
    return base

@router.get("/copilot/health")
def health():
    h = copilot_health()
    try:
        from app.services.rag import get_retriever
        h["rag"] = {"enabled": True, "docs": get_retriever().collection.count()}
    except Exception as e:
        h["rag"] = {"enabled": False, "error": str(e)}
    return h
