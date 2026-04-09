_refresh_job = None
"""Data pipeline API — live data refresh, country data access."""
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from app import external_data
from app.auth import require_api_key
from app.database import DataRefreshLog, get_db


router = APIRouter(
    prefix="/data",
    tags=["data-pipeline"],
    dependencies=[Depends(require_api_key)],
)


@router.post("/refresh")
def refresh_data(bg: BackgroundTasks):
    """Trigger live data refresh from World Bank API (runs in background)."""
    status = external_data.get_refresh_status()
    if status["status"] == "running":
        return {
            "status": "already_running",
            "message": "Refresh is in progress, check /data/refresh-status",
        }

    bg.add_task(external_data.refresh_live_data)
    return {
        "status": "started",
        "message": "Refresh started in background. Check /data/refresh-status for progress.",
    }


@router.get("/refresh/logs")
def refresh_logs(limit: int = 50, db: Session = Depends(get_db)):
    """Return recent data refresh log entries."""
    rows: List[DataRefreshLog] = (
        db.query(DataRefreshLog)
        .order_by(DataRefreshLog.timestamp.desc())
        .limit(limit)
        .all()
    )

    return [
        {
            "id": r.id,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "job_name": r.job_name,
            "status": r.status,
            "countries_fetched": r.countries_fetched,
            "total_countries": r.total_countries,
            "message": r.message,
            "source": r.source,
            "country_iso3": r.country_iso3,
            "indicator": r.indicator,
            "value": r.value,
            "error_message": r.error_message,
            "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None,
        }
        for r in rows
    ]


@router.get("/refresh/status")
@router.get("/refresh-status")
def refresh_job_status():
    """Check background refresh job status."""
    return external_data.get_refresh_status()


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
    country_name = name.strip().title()
    context = external_data.get_country_context(country_name)

    if not context:
        merged = external_data.get_merged_country_data(country_name)
        if merged:
            return {
                "country": country_name,
                "indicators": merged,
                "source": "merged",
            }
        return {
            "country": country_name,
            "error": f"Country '{country_name}' not found",
            "supported": external_data.get_supported_countries(),
        }

    context.setdefault("country", country_name)
    return context


@router.get("/countries/supported")
def supported_countries():
    """List of all supported country names."""
    return external_data.get_supported_countries()
