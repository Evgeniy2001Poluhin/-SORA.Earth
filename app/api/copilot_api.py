"""Co-Pilot API with RAG + Compliance Sentinel."""
from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import logging
from app.services.copilot import explain_prediction, health as copilot_health
from app.services.compliance import check as compliance_check

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
    cpd = features.get("co2_per_dollar")
    if co2: parts.append(f"CO2 reduction {int(co2)} t/yr")
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

def _scan_text(base):
    parts = [base.get("recommendation") or ""]
    risks = base.get("risks") or []
    parts.extend(risks)
    return " ".join(p for p in parts if p).strip()

@router.post("/copilot/explain")
def explain(payload: CopilotRequest, k: int = Query(4, ge=1, le=8)):
    base = explain_prediction(probability=payload.probability, features=payload.features, shap_values=payload.shap_values, project=payload.project, model_version=payload.model_version or "v5")
    q = _build_rag_query(payload.probability, payload.features)
    sources = _retrieve_sources(q, k=k)
    if isinstance(base, dict):
        base["sources"] = sources
        base["rag_query"] = q
        scan_text = _scan_text(base)
        base["compliance"] = compliance_check(scan_text) if scan_text else {"passed": True, "pii_findings": [], "bias_findings": [], "policy_violations": [], "redacted_text": "", "risk_score": 0.0, "engine": "regex-v1"}
    return base

@router.get("/copilot/health")
def health():
    h = copilot_health()
    try:
        from app.services.rag import get_retriever
        h["rag"] = {"enabled": True, "docs": get_retriever().collection.count()}
    except Exception as e:
        h["rag"] = {"enabled": False, "error": str(e)}
    h["compliance"] = {"enabled": True, "engine": "regex-v1"}
    return h



from fastapi.responses import StreamingResponse
import asyncio, json
_SPEED_MAP = {"fast": 0.015, "normal": 0.035, "slow": 0.08}


@router.post("/copilot/explain/stream")
async def explain_stream(payload: CopilotRequest, k: int = Query(4, ge=1, le=8), speed: str = Query("normal")):
    """SSE streaming variant of /copilot/explain."""
    base = explain_prediction(
        probability=payload.probability, features=payload.features,
        shap_values=payload.shap_values, project=payload.project,
        model_version=payload.model_version or "v5",
    )
    q = _build_rag_query(payload.probability, payload.features)
    sources = _retrieve_sources(q, k=k)
    scan_text = _scan_text(base) if isinstance(base, dict) else ""
    compliance = compliance_check(scan_text) if scan_text else {
        "passed": True, "pii_findings": [], "bias_findings": [],
        "policy_violations": [], "redacted_text": "", "risk_score": 0.0,
        "engine": "regex-v1",
    }
    rec = (base.get("recommendation") or "") if isinstance(base, dict) else ""
    if rec and sources:
        cites_text = " ".join(["[" + s.get("id", "?") + "]" for s in sources[:2]])
        rec = rec.rstrip() + " " + cites_text
    exs = (base.get("executive_summary") or "") if isinstance(base, dict) else ""

    async def gen():
        yield "data: " + json.dumps({"type": "meta", "probability": payload.probability, "rag_query": q}) + "\n\n"
        if exs:
            yield "data: " + json.dumps({"type": "section", "name": "executive_summary"}) + "\n\n"
            for w in exs.split(" "):
                yield "data: " + json.dumps({"type": "token", "value": w + " "}) + "\n\n"
                await asyncio.sleep(_SPEED_MAP.get(speed, 0.035))
        if rec:
            yield "data: " + json.dumps({"type": "section", "name": "recommendation"}) + "\n\n"
            for w in rec.split(" "):
                yield "data: " + json.dumps({"type": "token", "value": w + " "}) + "\n\n"
                await asyncio.sleep(_SPEED_MAP.get(speed, 0.035))
        yield "data: " + json.dumps({"type": "done", "sources": sources, "compliance": compliance}) + "\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
