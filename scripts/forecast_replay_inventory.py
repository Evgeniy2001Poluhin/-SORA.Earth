#!/usr/bin/env python3
"""Print an offline inventory to stdout. Never changes a cache or makes HTTP calls."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.forecasting.replay_availability_inventory import inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--state-sha256', required=True)
    parser.add_argument('--plan-sha256', required=True)
    args = parser.parse_args()
    result = inventory(args.cache, state_sha256=args.state_sha256, plan_sha256=args.plan_sha256)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
