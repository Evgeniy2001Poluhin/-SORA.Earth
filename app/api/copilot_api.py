"""Co-Pilot API with smart template-based explanations, RAG + Compliance Sentinel."""
from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import logging
from app.services.copilot import (
    explain_prediction,
    health as copilot_health,
    answer_qa,
    audience_directive,
    stream_explanation_hf,
    _hf_enabled,
)
from starlette.concurrency import iterate_in_threadpool
from app.services.compliance import check as compliance_check

router = APIRouter()
log = logging.getLogger(__name__)

class CopilotRequest(BaseModel):
    probability: float
    features: Dict[str, Any]
    shap_values: Optional[List[Dict[str, Any]]] = None
    project: Optional[Dict[str, Any]] = None
    model_version: Optional[str] = "v5"
    session_id: Optional[str] = None
    audience: Optional[str] = "executive"

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
        sid = _persist_explain(payload, base, sources)
        if sid:
            base["session_id"] = sid
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
    """SSE streaming variant of /copilot/explain.

    Uses smart template-based explanations with word-by-word streaming effect.
    No LLM required - templates are selected based on probability level and top negative factor.
    """
    use_hf = False  # HuggingFace removed, always use templates
    # With HF we stream the summary live, so skip the (blocking) enrichment call here.
    base = explain_prediction(
        probability=payload.probability, features=payload.features,
        shap_values=payload.shap_values, project=payload.project,
        model_version=payload.model_version or "v5",
        enrich=not use_hf,
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
        yield "data: " + json.dumps({"type": "meta", "probability": payload.probability, "rag_query": q, "session_id": sid}) + "\n\n"
        summary_text = ""
        if use_hf:
            yield "data: " + json.dumps({"type": "section", "name": "executive_summary"}) + "\n\n"
            try:
                # requests is blocking — iterate the sync HF generator via a threadpool
                # so the event loop stays responsive.
                async for text in iterate_in_threadpool(stream_explanation_hf(
                    base, payload.features, payload.project, payload.probability,
                )):
                    summary_text += text
                    yield "data: " + json.dumps({"type": "token", "value": text}) + "\n\n"
            except Exception as e:
                log.warning("HuggingFace stream failed: %s", e)
                err = f"[Co-Pilot LLM unavailable: {e}]"
                yield "data: " + json.dumps({"type": "token", "value": err}) + "\n\n"
        elif exs:
            summary_text = exs
            yield "data: " + json.dumps({"type": "section", "name": "executive_summary"}) + "\n\n"
            for w in exs.split(" "):
                yield "data: " + json.dumps({"type": "token", "value": w + " "}) + "\n\n"
                await asyncio.sleep(_SPEED_MAP.get(speed, 0.035))
        if rec:
            yield "data: " + json.dumps({"type": "section", "name": "recommendation"}) + "\n\n"
            for w in rec.split(" "):
                yield "data: " + json.dumps({"type": "token", "value": w + " "}) + "\n\n"
                await asyncio.sleep(_SPEED_MAP.get(speed, 0.035))
        # Persist after the summary is known so the streamed narrative is saved to the session.
        if summary_text and isinstance(base, dict):
            base["executive_summary"] = summary_text
        yield "data: " + json.dumps({"type": "done", "sources": sources, "compliance": compliance}) + "\n\n"

    sid = _persist_explain(payload, base, sources)
    return StreamingResponse(gen(), media_type="text/event-stream")


# --- Sessions ---
from app.services.sessions import (
    create_session as _ses_create,
    add_message as _ses_add,
    get_session as _ses_get,
    list_sessions as _ses_list,
    delete_session as _ses_del,
)
from fastapi import HTTPException


@router.get("/copilot/sessions")
def sessions_list(limit: int = Query(50, ge=1, le=200)):
    return {"sessions": _ses_list(limit=limit)}


@router.get("/copilot/sessions/{session_id}")
def sessions_get(session_id: str):
    s = _ses_get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    return s


@router.delete("/copilot/sessions/{session_id}")
def sessions_delete(session_id: str):
    ok = _ses_del(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="session not found")
    return {"deleted": True, "id": session_id}


def _persist_explain(payload, base, sources):
    """Save explain interaction to a session. Creates one if needed."""
    try:
        sid = payload.session_id or _ses_create(title=None)
        user_summary = (
            f"Explain p={payload.probability:.2f} "
            f"co2={payload.features.get('co2_reduction','-')} "
            f"budget={payload.features.get('budget','-')}"
        )
        _ses_add(sid, "user", user_summary)
        rec = (base.get("recommendation") or "") if isinstance(base, dict) else ""
        cites = [s.get("id") for s in (sources or []) if s.get("id")]
        _ses_add(sid, "assistant", rec, citations=cites)
        return sid
    except Exception as e:
        log.warning("session persist failed: %s", e)
        return None


class QARequest(BaseModel):
    session_id: str
    question: str
    audience: Optional[str] = "executive"
    k: Optional[int] = 4


class QAResponse(BaseModel):
    session_id: str
    answer: str
    sources: List[Dict[str, Any]]
    audience: str
    mode: str
    tokens_used: int


@router.post("/copilot/qa", response_model=QAResponse)
def qa(payload: QARequest):
    sess = _ses_get(payload.session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="session not found")
    msgs = sess.get("messages", [])
    last_a = next((m for m in reversed(msgs) if m.get("role") == "assistant"), None)
    context = (last_a or {}).get("content", "")
    sources = _retrieve_sources(payload.question, k=payload.k or 4)
    result = answer_qa(
        question=payload.question,
        context=context,
        sources=sources,
        audience=payload.audience or "executive",
    )
    try:
        _ses_add(payload.session_id, "user", payload.question)
        _ses_add(payload.session_id, "assistant", result["answer"],
                 citations=[s.get("id") for s in sources if s.get("id")])
    except Exception as e:
        log.warning("qa persist failed: %s", e)
    return QAResponse(
        session_id=payload.session_id,
        answer=result["answer"],
        sources=sources,
        audience=payload.audience or "executive",
        mode=result["mode"],
        tokens_used=result["tokens_used"],
    )
