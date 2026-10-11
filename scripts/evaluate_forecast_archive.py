#!/usr/bin/env python3
"""Run the M3-A evaluation on the archived Open-Meteo Historical Forecast series.

This is a **pseudo-real-time** evaluation (M2 protocol §2.2) on a fixed vintage
of the Open-Meteo Historical Forecast API, downloaded 2026-10-11. It is NOT
the live series production collects, and every result carries the label:

    "pseudo-real-time · archive vintage 2026-10-11 · not a real-time claim"

**Preregistered:** docs/M3A_ARCHIVE_DECLARATION.md was committed before the
first run on this data. Every constant, baseline, candidate, window placement
rule, and success criterion was fixed before any result existed.

Usage:
    python scripts/evaluate_forecast_archive.py --archive-dir ~/sora-archive-2026-10-11
    python scripts/evaluate_forecast_archive.py --archive-dir ~/sora-archive-2026-10-11 -o report.json

The archive directory must contain a `weather/` subdirectory with gzipped JSON
files: `weather/<POINT>_<YEAR>.json.gz`, each holding Open-Meteo Historical
Forecast API responses.

Results are deterministic (apart from the `generated_at` timestamp): two runs
over the same archive produce the same metrics, verdicts, and digests.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Add repo root to path so imports work when run as a script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.forecasting.archive_eval import evaluate_archive


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--archive-dir",
        required=True,
        help="Path to the archive directory (must contain weather/ subdirectory)",
    )
    parser.add_argument(
        "-o", "--output",
        help="Also write the report to this file (JSON)",
    )
    args = parser.parse_args(argv)

    report = evaluate_archive(args.archive_dir)

    text_out = json.dumps(report, indent=2, sort_keys=True, default=str)
    print(text_out)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text_out + "\n")

    # A refusal is not a failure: it is the report's content. Exit 0 either way.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
