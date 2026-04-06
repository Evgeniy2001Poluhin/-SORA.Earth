"""Data pipeline API — live data refresh, country data access."""
from fastapi import APIRouter, BackgroundTasks, Depends
from app.auth import require_api_key
from app import external_data

router = APIRouter(prefix="/data", tags=["data-pipeline"])

_refresh_job = {"running": False, "result": None}


def _run_refresh():
    _refresh_job["running"] = True
    try:
        _refresh_job["result"] = external_data.refresh_live_data()
    except Exception as e:
        _refresh_job["result"] = {"status": "error", "error": str(e)}
    _refresh_job["running"] = False


@router.post("/refresh")
def refresh_data(bg: BackgroundTasks):
    """Trigger live data refresh from World Bank API (runs in background)."""
    if _refresh_job["running"]:
        return {"status": "already_running", "message": "Refresh is in progress, check /data/refresh-status"}
    bg.add_task(_run_refresh)
    return {"status": "started", "message": "Refresh started in background. Check /data/refresh-status for progress."}


@router.get("/refresh/status")
@router.get("/refresh-status")
def refresh_job_status():
    """Check background refresh job status."""
    return {
        "running": _refresh_job["running"],
        "last_result": _refresh_job["result"],
        **external_data.get_refresh_status(),
    }


@router.get("/status")
def data_status():
    """Status of the data pipeline."""
    return external_data.get_refresh_status()


@router.get("/countries")
def all_countries():
    """All countries with merged live + static ESG data."""
    data = external_data.get_all_countries_merged()
    return {"count": len(data), "countries": data}


@router.get("/country/{name}")
def single_country(name: str):
    """Single country ESG profile with full context."""
    context = external_data.get_country_context(name)
    if not context:
        merged = external_data.get_merged_country_data(name)
        if merged:
            return {"country": name.strip().title(), "indicators": merged, "source": "merged"}
        return {"error": f"Country '{name}' not found", "supported": external_data.get_supported_countries()}
    return context


@router.get("/countries/supported")
def supported_countries():
    """List of all supported country names."""
    return external_data.get_supported_countries()
