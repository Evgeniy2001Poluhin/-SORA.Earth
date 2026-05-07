from fastapi import APIRouter, HTTPException
from app.schemas import ProjectInput as Project
from app.services.compliance_engine import assess_csrd, ESRS_WEIGHTS

router = APIRouter(tags=["compliance"])

@router.get("/compliance/frameworks")
def list_frameworks():
    return {"frameworks": [{
        "id": "CSRD_ESRS",
        "name": "Corporate Sustainability Reporting Directive (EU)",
        "version": "Post-Omnibus I (2026)",
        "categories": list(ESRS_WEIGHTS.keys()),
        "weights": ESRS_WEIGHTS,
        "mandatory_from": "FY2027 (>1000 employees, >450M EUR turnover)",
    }]}

@router.post("/compliance/csrd")
def csrd_check(project: Project):
    try:
        return assess_csrd(project)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"CSRD failed: {e}")

@router.post("/compliance/gap-analysis")
def gap_analysis(project: Project):
    r = assess_csrd(project)
    return {
        "project_name": r["project_name"],
        "overall_readiness": r["overall_readiness"],
        "status": r["status"],
        "audit_ready": r["audit_ready"],
        "missing_evidence": r["missing_evidence"],
        "recommended_actions": r["recommended_actions"],
    }
