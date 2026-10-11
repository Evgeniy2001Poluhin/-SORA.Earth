"""Backfill environmental observations from Open-Meteo historical archives.

Fetches hourly weather and air quality data from Open-Meteo APIs to fill gaps
caused by server losses (the 2026-08-16 and 2026-10-10 deletions).

## Why a separate database

The archive holds ~16 million rows (weather + air quality, 2018-01-01 onward).
Daily backups dump only the main application database and are pulled to the
owner's laptop; 16M rows would make every dump ~0.5 GB. The archive is stored
in a separate database (e.g., sora_archive) outside the daily backup rotation.

The data is reproducible: Open-Meteo's Historical Forecast and Air Quality
APIs serve the same quantities after the fact, so the archive can be regenerated
if lost. Main-database observations cannot.

## Usage

    # Inside the backend container, create the archive database first:
    createdb -U sora sora_archive

    # Backfill weather only, dry run:
    python scripts/backfill_openmeteo_archive.py \\
        --database-url postgresql://sora:password@postgres:5432/sora_archive \\
        --start 2018-01-01 --end 2018-12-31 --kind weather --dry-run

    # Backfill from cached archive:
    python scripts/backfill_openmeteo_archive.py \\
        --database-url postgresql://sora:password@postgres:5432/sora_archive \\
        --start 2018-01-01 --end 2020-12-31 --kind both \\
        --from-dir /path/to/archive --vintage 2026-10-11

## Processing

Ranges are processed in chunks (one calendar year per point per kind) to bound
memory usage. An interrupted run can be resumed: the upsert makes it idempotent,
and progress is printed per chunk. The tool refuses to write to the main
application database.

## Source provenance

Archive-derived rows carry distinct source names (openmeteo_archive,
openmeteo_air_quality_archive) and are never mistaken for live ingestion. The
source_revision tag is "archive-fetched-<YYYY-MM-DD>" (UTC date of the fetch or
--vintage), so re-fetches on different dates produce distinct vintages.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import EnvironmentalObservation
from app.ingesters.openmeteo_archive import (
    fetch_weather_archive,
    fetch_air_quality_archive,
    fetch_weather_for_point_year,
    fetch_air_quality_for_point_year,
    compute_archive_hash,
    end_of_day,
    REGION_CAPITALS,
    AIR_QUALITY_ARCHIVE_START,
)
from app.ingesters.persist import persist_environmental_observations

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def parse_date(date_str: str) -> datetime:
    """Parse YYYY-MM-DD date string to UTC datetime."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as e:
        raise ValueError(f"Invalid date format '{date_str}': expected YYYY-MM-DD") from e


def validate_date_range(start: datetime, end: datetime) -> None:
    """Validate date range constraints."""
    if end < start:
        raise ValueError(f"End date {end.date()} is before start date {start.date()}")

    delta_days = (end - start).days
    max_days = 10 * 365

    if delta_days > max_days:
        raise ValueError(
            f"Date range spans {delta_days} days (> {max_days} days / ~10 years). "
            f"Split into smaller ranges to avoid memory issues."
        )


def parse_database_components(database_url: str) -> tuple[str, str]:
    """Parse database name and host from connection URL."""
    parsed = urlparse(database_url)
    db_name = parsed.path.lstrip("/") if parsed.path else ""
    host = parsed.hostname or "localhost"
    return db_name, host


def check_database_separation(target_url: str) -> None:
    """Refuse if target database is the main application database."""
    app_db_url = os.environ.get("DATABASE_URL")
    if not app_db_url:
        log.warning("DATABASE_URL not set; cannot verify database separation")
        return

    target_db, target_host = parse_database_components(target_url)
    app_db, app_host = parse_database_components(app_db_url)

    localhost_aliases = {"localhost", "127.0.0.1", "::1"}
    target_host_normalized = "localhost" if target_host in localhost_aliases else target_host
    app_host_normalized = "localhost" if app_host in localhost_aliases else app_host

    if target_db == app_db and target_host_normalized == app_host_normalized:
        log.error(
            f"REFUSED: target database '{target_db}' at '{target_host}' "
            f"is the main application database.\n\n"
            f"The archive holds ~16M rows. Daily backups dump only the main database "
            f"and are pulled to the owner's laptop; this would make every dump ~0.5 GB.\n\n"
            f"The archive data is reproducible from Open-Meteo APIs, so it is deliberately "
            f"kept in a separate database outside the daily backup rotation.\n\n"
            f"Create a separate database (e.g., sora_archive) and pass its URL:\n"
            f"  createdb -U sora sora_archive\n"
            f"  python {sys.argv[0]} --database-url postgresql://sora:...@host/sora_archive ..."
        )
        sys.exit(1)


def ensure_table_exists(engine) -> None:
    """Create environmental_observations table if it doesn't exist."""
    try:
        EnvironmentalObservation.__table__.create(bind=engine, checkfirst=True)
        log.info("Archive table ready (created or already exists)")
    except Exception as e:
        log.error(f"Failed to create table: {e}")
        log.error("Ensure the database exists (e.g., createdb -U sora sora_archive)")
        sys.exit(1)


def split_into_year_chunks(
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, datetime]]:
    """Split date range into calendar-year chunks."""
    chunks = []
    current_year = start.year

    while True:
        chunk_start = max(start, datetime(current_year, 1, 1, tzinfo=timezone.utc))
        year_end = end_of_day(datetime(current_year, 12, 31, tzinfo=timezone.utc))
        chunk_end = min(end, year_end)

        if chunk_start > end:
            break

        chunks.append((chunk_start, chunk_end))

        if chunk_end >= end:
            break

        current_year += 1

    return chunks


def estimate_api_calls(chunks: list, kind: str, num_points: int) -> int:
    """Estimate weighted API calls for Open-Meteo requests.

    Open-Meteo weights a request as roughly (days / 14) × (variables / 10).
    Weather has 10 variables, air quality has 6.
    """
    total_calls = 0

    for chunk_start, chunk_end in chunks:
        days = (chunk_end - chunk_start).days + 1

        if kind in ("weather", "both"):
            # (days / 14) × (10 vars / 10) = days / 14
            total_calls += num_points * (days / 14.0) * (10 / 10.0)

        if kind in ("air_quality", "both"):
            # (days / 14) × (6 vars / 10) = days × 6 / 140
            total_calls += num_points * (days / 14.0) * (6 / 10.0)

    return int(total_calls)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--database-url",
        required=True,
        help="Archive database URL (e.g., postgresql://sora:...@host/sora_archive). "
             "Must NOT be the main application database.",
    )
    parser.add_argument(
        "--start",
        required=True,
        help="Start date (YYYY-MM-DD, inclusive)",
    )
    parser.add_argument(
        "--end",
        required=True,
        help="End date (YYYY-MM-DD, inclusive)",
    )
    parser.add_argument(
        "--kind",
        choices=["weather", "air_quality", "both"],
        default="both",
        help="What to backfill (default: both)",
    )
    parser.add_argument(
        "--from-dir",
        type=Path,
        help="Load cached responses from this directory instead of fetching",
    )
    parser.add_argument(
        "--vintage",
        help="Download date (YYYY-MM-DD) for source_revision; required with --from-dir",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and build signals, print stats, write nothing",
    )
    parser.add_argument(
        "--points",
        help="Comma-separated region codes to process (e.g., DEU,GBR,FRA). Default: all 21 points.",
    )

    args = parser.parse_args(argv)

    # Parse and validate --points
    if args.points:
        requested_codes = [code.strip() for code in args.points.split(',')]
        valid_codes = {rc for rc, _, _ in REGION_CAPITALS}
        invalid_codes = set(requested_codes) - valid_codes
        
        if invalid_codes:
            log.error(
                f"Unknown region codes: {', '.join(sorted(invalid_codes))}\n\n"
                f"Valid codes: {', '.join(sorted(valid_codes))}"
            )
            return 1
        
        # Filter REGION_CAPITALS to only the requested points
        selected_points = [
            (rc, lat, lon) for rc, lat, lon in REGION_CAPITALS
            if rc in requested_codes
        ]
    else:
        selected_points = list(REGION_CAPITALS)

    if args.from_dir and not args.vintage:
        log.error("--vintage is required when using --from-dir")
        return 1

    try:
        start_date = parse_date(args.start)
        end_date = end_of_day(parse_date(args.end))
        validate_date_range(start_date, end_date)
    except ValueError as e:
        log.error(str(e))
        return 1

    if args.kind in ("air_quality", "both") and start_date < AIR_QUALITY_ARCHIVE_START:
        log.error(
            f"Air quality archive starts {AIR_QUALITY_ARCHIVE_START.date()}. "
            f"Requested start {start_date.date()} is before archive coverage.\n"
            f"Adjust --start to 2022-08-01 or later, or use --kind weather."
        )
        return 1

    vintage = None
    if args.vintage:
        try:
            vintage = parse_date(args.vintage)
        except ValueError as e:
            log.error(f"Invalid --vintage: {e}")
            return 1

    if not args.dry_run:
        check_database_separation(args.database_url)

    if not args.dry_run:
        try:
            engine = create_engine(args.database_url, pool_pre_ping=True)
            ensure_table_exists(engine)
            Session = sessionmaker(bind=engine)
        except Exception as e:
            target_db, target_host = parse_database_components(args.database_url)
            log.error(f"Failed to connect to archive database '{target_db}' at '{target_host}': {e}")
            log.error("Ensure the database exists (e.g., createdb -U sora sora_archive)")
            return 1

    chunks = split_into_year_chunks(start_date, end_date)
    log.info(f"Processing {len(chunks)} year chunk(s) from {start_date.date()} to {end_date.date()}")
    log.info(f"Selected points ({len(selected_points)}): {', '.join(rc for rc, _, _ in selected_points)}")
    log.info(f"Mode: {'DRY RUN (no writes)' if args.dry_run else 'PERSIST'}")
    log.info(f"Kind: {args.kind}")

    if args.from_dir:
        log.info(f"From cached: {args.from_dir}")
        log.info(f"Vintage: {args.vintage}")
    else:
        estimated_calls = estimate_api_calls(chunks, args.kind, len(selected_points))
        log.info(f"Estimated weighted API calls: ~{estimated_calls}")
        log.info(f"Free tier limits: 600/min, 5,000/hour, 10,000/day")
        
        if estimated_calls > 4000:
            log.error(
                f"Estimated {estimated_calls} API calls exceeds safe limit (4,000).\n\n"
                f"Arithmetic: Open-Meteo counts ~2.6 weighted calls per variable per month.\n"
                f"  Weather: 10 vars × {len(chunks)} year-chunks × {len(selected_points)} points × 12 months × 2.6 = {int(10 * len(chunks) * len(selected_points) * 12 * 2.6)} calls\n"
                f"  At 5s delay (12 req/min), this would consume {int(estimated_calls/12)} minutes\n"
                f"  and exhaust hourly (5,000) or daily (10,000) limits.\n\n"
                f"Download the archive first and use --from-dir:\n"
                f"  1. Download responses to a local directory (structure: weather/<POINT>_<YEAR>.json.gz)\n"
                f"  2. Run: python {sys.argv[0]} --from-dir /path/to/archive --vintage YYYY-MM-DD ..."
            )
            return 1

    total_signals = 0
    total_none_skipped = 0
    total_inserted = 0
    total_updated = 0
    total_rejected = 0
    failed_fetches = []
    failed_persists = []
    all_files_read = []

    for chunk_idx, (chunk_start, chunk_end) in enumerate(chunks, 1):
        log.info(f"\n--- Chunk {chunk_idx}/{len(chunks)}: {chunk_start.date()} to {chunk_end.date()} ---")

        # Process one (point, year) at a time to bound memory
        for region_code, lat, lon in selected_points:
            for year in range(chunk_start.year, chunk_end.year + 1):
                year_start = max(chunk_start, datetime(year, 1, 1, tzinfo=timezone.utc))
                year_end = min(chunk_end, end_of_day(datetime(year, 12, 31, tzinfo=timezone.utc)))

                if year_start > chunk_end or year_end < chunk_start:
                    continue

                # Weather
                if args.kind in ("weather", "both"):
                    log.info(f"  Processing weather: {region_code} year {year}")
                    try:
                        weather_signals, weather_stats = fetch_weather_for_point_year(
                            region_code, lat, lon, year,
                            year_start, year_end,
                            from_dir=args.from_dir,
                            vintage=vintage,
                        )

                        total_signals += len(weather_signals)
                        total_none_skipped += weather_stats["none_values_skipped"]

                        if args.from_dir:
                            all_files_read.extend(weather_stats.get("files_read", []))
                            missing = weather_stats.get("missing_years", [])
                            if missing:
                                for rc, yr, kind in missing:
                                    failed_fetches.append(("weather", rc, yr, f"missing file {rc}_{yr}.json.gz"))

                        log.info(
                            f"    → {weather_stats['signals_produced']} signals, "
                            f"{weather_stats['none_values_skipped']} None values skipped"
                        )

                        # Persist immediately if not dry run
                        if not args.dry_run and weather_signals:
                            session = Session()
                            try:
                                result = persist_environmental_observations(
                                    weather_signals, "openmeteo_archive", session=session
                                )
                                session.commit()

                                total_inserted += result.inserted
                                total_updated += result.updated
                                total_rejected += result.rejected

                                log.info(
                                    f"    → Persisted: {result.inserted} inserted, "
                                    f"{result.updated} updated, {result.duplicates} duplicates, "
                                    f"{result.rejected} rejected"
                                )

                                if result.errors:
                                    for error in result.errors[:3]:
                                        log.warning(f"      {error}")
                                    if len(result.errors) > 3:
                                        log.warning(f"      ... and {len(result.errors) - 3} more errors")
                            except Exception as e:
                                session.rollback()
                                log.error(f"    → Failed to persist weather for {region_code} year {year}: {e}")
                                failed_persists.append(("openmeteo_archive", region_code, year, str(e)))
                            finally:
                                session.close()
                        elif args.dry_run and weather_signals:
                            log.info(f"    → DRY RUN: would persist {len(weather_signals)} signals")

                    except Exception as e:
                        log.error(f"  Weather fetch failed for {region_code} year {year}: {e}")
                        failed_fetches.append(("weather", region_code, year, str(e)))

                # Air quality
                if args.kind in ("air_quality", "both"):
                    log.info(f"  Processing air quality: {region_code} year {year}")
                    try:
                        aq_signals, aq_stats = fetch_air_quality_for_point_year(
                            region_code, lat, lon, year,
                            year_start, year_end,
                            from_dir=args.from_dir,
                            vintage=vintage,
                        )

                        total_signals += len(aq_signals)
                        total_none_skipped += aq_stats["none_values_skipped"]

                        if args.from_dir:
                            all_files_read.extend(aq_stats.get("files_read", []))
                            missing = aq_stats.get("missing_years", [])
                            if missing:
                                for rc, yr, kind in missing:
                                    failed_fetches.append(("air_quality", rc, yr, f"missing file {rc}_{yr}.json.gz"))

                        log.info(
                            f"    → {aq_stats['signals_produced']} signals, "
                            f"{aq_stats['none_values_skipped']} None values skipped"
                        )

                        # Persist immediately if not dry run
                        if not args.dry_run and aq_signals:
                            session = Session()
                            try:
                                result = persist_environmental_observations(
                                    aq_signals, "openmeteo_air_quality_archive", session=session
                                )
                                session.commit()

                                total_inserted += result.inserted
                                total_updated += result.updated
                                total_rejected += result.rejected

                                log.info(
                                    f"    → Persisted: {result.inserted} inserted, "
                                    f"{result.updated} updated, {result.duplicates} duplicates, "
                                    f"{result.rejected} rejected"
                                )

                                if result.errors:
                                    for error in result.errors[:3]:
                                        log.warning(f"      {error}")
                                    if len(result.errors) > 3:
                                        log.warning(f"      ... and {len(result.errors) - 3} more errors")
                            except Exception as e:
                                session.rollback()
                                log.error(f"    → Failed to persist air quality for {region_code} year {year}: {e}")
                                failed_persists.append(("openmeteo_air_quality_archive", region_code, year, str(e)))
                            finally:
                                session.close()
                        elif args.dry_run and aq_signals:
                            log.info(f"    → DRY RUN: would persist {len(aq_signals)} signals")

                    except Exception as e:
                        log.error(f"  Air quality fetch failed for {region_code} year {year}: {e}")
                        failed_fetches.append(("air_quality", region_code, year, str(e)))


    log.info("\n" + "=" * 60)
    log.info("SUMMARY")
    log.info("=" * 60)
    log.info(f"Date range: {start_date.date()} to {end_date.date()}")
    log.info(f"Chunks processed: {len(chunks)}")
    log.info(f"Total signals: {total_signals}")
    log.info(f"None values skipped: {total_none_skipped}")

    if args.dry_run:
        log.info("DRY RUN: nothing was written")
    else:
        log.info(f"Inserted: {total_inserted}")
        log.info(f"Updated: {total_updated}")
        log.info(f"Rejected: {total_rejected}")
        log.info(f"Total accepted: {total_inserted + total_updated}")

    if args.from_dir and all_files_read:
        archive_hash = compute_archive_hash(all_files_read)
        log.info(f"Archive files read: {len(set(all_files_read))}")
        log.info(f"Archive hash (sha256): {archive_hash}")

    if failed_fetches:
        log.error(f"\nFailed fetches: {len(failed_fetches)}")
        for kind, point_or_chunk, year, error in failed_fetches:
            log.error(f"  {kind} {point_or_chunk} year {year}: {error}")

    if failed_persists:
        log.error(f"\nFailed persists: {len(failed_persists)}")
        for source, region_code, year, error in failed_persists:
            log.error(f"  {source} {region_code} year {year}: {error}")

    if failed_fetches or failed_persists:
        log.error("\nRun FAILED due to errors above")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
