"""Environmental observation persistence with upsert logic.

Implements INSERT ... ON CONFLICT DO UPDATE for idempotent ingestion.
Tracks inserted/updated/duplicate records for accurate health classification.
"""
from __future__ import annotations
import logging, json
from datetime import datetime, timezone
from typing import List, Dict, Any
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from app.database import SessionLocal, EnvironmentalObservation
from app.ingesters.base import Signal
from app.ingesters import temporal

log = logging.getLogger(__name__)


@dataclass
class PersistResult:
    """Result of persist operation with detailed stats."""
    received: int
    inserted: int
    updated: int
    rejected: int
    duplicates: int  # Records that matched conflict key but had no changes
    accepted: int  # inserted + updated
    errors: List[str]


def persist_environmental_observations(
    signals: List[Signal],
    source: str
) -> PersistResult:
    """Persist environmental signals with upsert logic.

    Uses PostgreSQL INSERT ... ON CONFLICT DO UPDATE to handle duplicates.
    Conflict key: (source, source_record_id, event_time)

    Args:
        signals: List of Signal objects to persist
        source: Data source name (e.g., "openaq", "openmeteo")

    Returns:
        PersistResult with inserted/updated/rejected counts
    """
    db = SessionLocal()
    result = PersistResult(
        received=len(signals),
        inserted=0,
        updated=0,
        rejected=0,
        duplicates=0,
        accepted=0,
        errors=[]
    )

    if not signals:
        db.close()
        return result

    try:
        # Build observations batch
        observations = []
        for idx, signal in enumerate(signals):
            try:
                obs = _signal_to_observation_dict(signal, source)
                observations.append(obs)
            except Exception as e:
                result.rejected += 1
                result.errors.append(f"signal_{idx}: {str(e)[:200]}")
                log.warning(f"[persist] rejected signal {idx}: {e}")

        # Execute upsert. The counts stay local until the commit succeeds: a
        # failure below rolls the transaction back, and a result that still
        # claimed accepted > 0 would be reporting rows that no longer exist.
        # app/ingesters/runner.py writes result["accepted"] into
        # ingester_runs.rows_written, so the lie would be recorded as history.
        inserted = updated = duplicates = 0
        if observations:
            inserted, updated, duplicates = _upsert_observations(db, observations)

        db.commit()

        # Committed: the counts now describe rows that are actually there.
        result.inserted = inserted
        result.updated = updated
        result.duplicates = duplicates
        result.accepted = inserted + updated
        log.info(
            f"[persist] {source}: received={result.received}, "
            f"inserted={result.inserted}, updated={result.updated}, "
            f"duplicates={result.duplicates}, rejected={result.rejected}"
        )

    except Exception as e:
        db.rollback()
        # Nothing was written. `rejected` is kept -- those signals failed
        # validation before the database was involved and that is still true --
        # but everything describing stored rows is zeroed, because after the
        # rollback there are none.
        result.inserted = 0
        result.updated = 0
        result.duplicates = 0
        result.accepted = 0
        result.errors.append(f"persist_error: {str(e)[:500]}")
        log.exception(f"[persist] {source} failed: {e}")
    finally:
        db.close()

    return result


def _as_utc(value):
    """Naive datetimes are read as UTC, as they always were here."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _coordinates(metadata):
    """The point this observation was taken at, or nothing.

    `latitude` and `longitude` were NULL on every row of every source (#84).
    The columns existed, the two geographic ingesters knew where they were
    reading, and the value never crossed from one to the other -- so a reader
    had to parse free-form JSON, and only for the sources that happened to
    record it there.

    NULL keeps a meaning after this: rosstat and sber_veb_baseline publish
    region-level figures that are not taken anywhere, and a coordinate invented
    for them would be a claim about where a number was measured.

    Three ways to refuse, each a real shape:

    - one coordinate without the other is not a location. Storing the half
      that arrived puts the row on the equator or the prime meridian, which
      are places.
    - a value outside the valid range is not a coordinate. It cannot be
      clamped -- clamping moves a broken reading to a real point on the map,
      where it is indistinguishable from a good one.
    - a value that is not a number at all, which is what a source returning
      `"unknown"` or `null` produces.
    """
    if not metadata:
        return None, None

    lat, lon = metadata.get("latitude"), metadata.get("longitude")
    if lat is None or lon is None:
        return None, None

    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None

    # NaN fails every comparison, so it passes a range check written as
    # `not (lat < -90 or lat > 90)`. Asked positively it is excluded.
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        log.warning(
            "coordinates out of range for %s: (%s, %s); stored as absent",
            metadata.get("source") or "an observation", lat, lon,
        )
        return None, None

    return lat, lon


def _signal_to_observation_dict(signal: Signal, source: str) -> Dict[str, Any]:
    """Convert Signal to EnvironmentalObservation dict for insert.

    Args:
        signal: Signal object from ingester
        source: Data source name

    Returns:
        Dict with EnvironmentalObservation columns

    Raises:
        ValueError: If required fields are missing or invalid
    """
    if not signal.region_code:
        raise ValueError("region_code required")
    if not signal.metric:
        raise ValueError("metric (indicator) required")
    if signal.value is None:
        raise ValueError("value required")

    # A missing observation time is no longer filled in with the current one.
    #
    # It used to be, and that single line is #121: a dict of 85 constants and an
    # offline 2024 snapshot were recorded as measured today. It also defeated
    # deduplication -- `event_time` went into `source_record_id`, so a fresh
    # stamp meant a fresh identity, the unique index never fired, and the same
    # unchanged literal was inserted again on every run.
    event_time = signal.observed_at
    if event_time is not None and event_time.tzinfo is None:
        # Assume UTC if naive datetime
        event_time = event_time.replace(tzinfo=timezone.utc)

    kind = getattr(signal, "temporal_kind", temporal.OBSERVED)
    period_start = _as_utc(getattr(signal, "period_start", None))
    period_end = _as_utc(getattr(signal, "period_end", None))
    source_revision = getattr(signal, "source_revision", None)

    # Refused rather than repaired. A default here is invisible in the data,
    # which is the property that let the original defect run for months.
    temporal.validate(kind, event_time=event_time, period_start=period_start,
                      period_end=period_end, source_revision=source_revision)

    # Derived from whichever fields make a row distinct for its kind, hashed
    # rather than concatenated: `_` occurs inside region codes and metric names,
    # so the old `{region}_{metric}_{time}` could not be parsed back apart.
    source_record_id = temporal.canonical_identity(
        source=source, region_code=signal.region_code, metric=signal.metric,
        kind=kind, event_time=event_time, period_start=period_start,
        period_end=period_end, source_revision=source_revision,
    )

    # Calculate quality score from metadata if available
    quality_score = None
    if signal.metadata:
        quality_flag = signal.metadata.get("quality")
        if quality_flag:
            # Map quality flags to 0-100 scale
            quality_map = {
                "excellent": 100.0,
                "good": 80.0,
                "fair": 60.0,
                "poor": 40.0,
                "invalid": 0.0,
            }
            quality_score = quality_map.get(quality_flag)

    # Determine validity
    is_valid = True
    if signal.metadata and signal.metadata.get("quality") == "invalid":
        is_valid = False

    latitude, longitude = _coordinates(signal.metadata)

    return {
        "region_id": signal.region_code,
        "country_code": signal.region_code[:3] if len(signal.region_code) >= 3 else None,
        "latitude": latitude,
        "longitude": longitude,
        "indicator": signal.metric,
        "value": float(signal.value),
        "unit": signal.unit,
        "source": source,
        "source_record_id": source_record_id,
        "source_revision": source_revision,
        "event_time": event_time,
        "temporal_kind": kind,
        "period_start": period_start,
        "period_end": period_end,
        "published_at": None,
        "ingested_at": datetime.now(timezone.utc),
        "quality_score": quality_score,
        "is_valid": is_valid,
        "metadata_json": json.dumps(signal.metadata) if signal.metadata else None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }


def _upsert_observations(
    db: Any,
    observations: List[Dict[str, Any]]
) -> tuple[int, int, int]:
    """Execute upsert with ON CONFLICT DO UPDATE (PostgreSQL) or fallback (SQLite).

    PostgreSQL uses RETURNING with xmax=0 check to distinguish INSERT vs UPDATE:
    - xmax=0 means new row was inserted
    - xmax!=0 means existing row was updated

    Args:
        db: SQLAlchemy session
        observations: List of observation dicts

    Returns:
        Tuple of (inserted_count, updated_count, duplicate_count)
    """
    if not observations:
        return (0, 0, 0)

    from sqlalchemy import literal_column

    inserted = 0
    updated = 0
    duplicates = 0

    # Check database dialect
    dialect_name = db.bind.dialect.name

    if dialect_name == "postgresql":
        # PostgreSQL: use native UPSERT with ON CONFLICT + RETURNING
        stmt = insert(EnvironmentalObservation).values(observations)

        # On conflict: update fields that may have changed
        update_dict = {
            "updated_at": stmt.excluded.updated_at,
            "quality_score": stmt.excluded.quality_score,
            "metadata_json": stmt.excluded.metadata_json,
            "value": stmt.excluded.value,  # Value may be updated/corrected
            "is_valid": stmt.excluded.is_valid,
            # Without these, a row written before #84 keeps its NULL
            # coordinates however many times the source re-states where the
            # reading was taken -- the fix would apply only to rows nobody had
            # ever seen, which is the smaller half of the problem.
            "latitude": stmt.excluded.latitude,
            "longitude": stmt.excluded.longitude,
        }

        # Use RETURNING with xmax check to distinguish INSERT vs UPDATE
        # xmax=0 indicates a newly inserted row (not an update)
        # index_where must match the partial unique index's predicate
        # (ix_environmental_observations_source_record_unique), or Postgres
        # can't infer it as the ON CONFLICT target.
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=["source", "source_record_id"],
            index_where=text("source_record_id IS NOT NULL"),
            set_=update_dict
        ).returning(
            EnvironmentalObservation.id,
            literal_column("(xmax = 0)").label("inserted")
        )

        # Execute upsert and count results
        result = db.execute(upsert_stmt)
        rows = result.fetchall()

        # Count inserted vs updated based on xmax flag
        inserted = sum(1 for r in rows if r.inserted)
        updated = len(rows) - inserted

        log.debug(
            f"[persist] PostgreSQL upsert: {inserted} inserted, "
            f"{updated} updated, {len(observations)} total"
        )

    else:
        # SQLite or other: fallback to manual upsert (check then insert/update)
        # Slower but works everywhere
        for obs in observations:
            try:
                # Check if observation exists
                existing = db.query(EnvironmentalObservation).filter(
                    EnvironmentalObservation.source == obs["source"],
                    EnvironmentalObservation.source_record_id == obs["source_record_id"]
                ).first()

                if existing:
                    # Update existing record
                    existing.value = obs["value"]
                    existing.quality_score = obs["quality_score"]
                    existing.is_valid = obs["is_valid"]
                    existing.metadata_json = obs["metadata_json"]
                    # Kept in step with the PostgreSQL update set above. The two
                    # branches diverging is how a property holds in the tests
                    # and not on the database anybody deploys.
                    existing.latitude = obs["latitude"]
                    existing.longitude = obs["longitude"]
                    existing.updated_at = obs["updated_at"]
                    updated += 1
                else:
                    # Insert new record
                    new_obs = EnvironmentalObservation(**obs)
                    db.add(new_obs)
                    inserted += 1

            except Exception as e:
                log.warning(f"[persist] fallback upsert failed for observation: {e}")
                continue

        log.debug(f"[persist] fallback upsert: {inserted} inserted, {updated} updated (SQLite)")

    return (inserted, updated, duplicates)


def calculate_health_status(
    persist_result: PersistResult,
    freshness_hours: float,
    missing_rate: float
) -> tuple[str, str]:
    """Calculate health status based on persist result and data quality metrics.

    Health criteria from ROADMAP requirements:
    - healthy: records_accepted > 0 AND freshness < 2h AND missing_rate < 20%
    - degraded: records_accepted > 0 BUT (freshness 2-6h OR missing_rate 20-50%)
    - unhealthy: records_accepted = 0 OR freshness > 6h OR missing_rate > 50%

    Args:
        persist_result: Result from persist operation
        freshness_hours: Time since last observation (hours)
        missing_rate: Percentage of missing/invalid observations (0-100)

    Returns:
        Tuple of (health_status, reason)
    """
    accepted = persist_result.accepted

    # Unhealthy conditions
    if accepted == 0:
        return ("unhealthy", "no_records_accepted")

    if freshness_hours > 6.0:
        return ("unhealthy", f"stale_data_{freshness_hours:.1f}h")

    if missing_rate > 50.0:
        return ("unhealthy", f"high_missing_rate_{missing_rate:.1f}%")

    # Degraded conditions
    if freshness_hours >= 2.0 and freshness_hours <= 6.0:
        return ("degraded", f"old_data_{freshness_hours:.1f}h")

    if missing_rate >= 20.0 and missing_rate <= 50.0:
        return ("degraded", f"moderate_missing_rate_{missing_rate:.1f}%")

    # Healthy
    return ("healthy", "all_checks_passed")
