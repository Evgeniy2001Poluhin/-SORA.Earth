#!/usr/bin/env python3
"""Read-only, credential-free evidence collector; never a deployment GO.

Run on the production host with Python's standard library. It reads only public
health, loopback Prometheus, selected systemd properties and dump metadata.
It does not open dumps, configuration secrets, account rows or service logs.
Unknown evidence is explicit; a fresh local dump is not an off-site backup.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlencode
from urllib.request import urlopen

LIMIT = 1024 * 1024
PROMETHEUS = "http://127.0.0.1:9090"
PAIR = 'job="sora-scheduler",source="openmeteo",indicator="temperature"'
# Declared clock in docs/M3_FORECAST_DECLARATION.md and observation_coverage.py.
CLOCK_START = date(2026, 10, 11)


def read_json(url):
    with urlopen(url, timeout=10) as response:
        body = response.read(LIMIT + 1)
    if len(body) > LIMIT:
        raise ValueError("response_too_large")
    return json.loads(body)


def query(expression):
    try:
        data = read_json(PROMETHEUS + "/api/v1/query?" + urlencode({"query": expression}))
        if data.get("status") != "success" or data["data"]["resultType"] != "vector":
            return {"status": "unknown", "reason": "invalid_prometheus_response"}
        rows = data["data"]["result"]
        values = [float(row["value"][1]) for row in rows]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("non_finite")
        return {"status": "observed", "values": values}
    except Exception:
        # Exception messages may contain arbitrary server content. Do not retain them.
        return {"status": "unknown", "reason": "query_failed"}


def evaluate(metrics, now):
    checks = {}
    expected_days = min(30, max(0, (datetime.fromtimestamp(now, timezone.utc).date() - CLOCK_START).days))
    for name, predicate in {
        "app_up": lambda value: value == 1,
        "scheduler_up": lambda value: value == 1,
        "gap_count": lambda value: value == 0,
        "days_examined": lambda value: expected_days > 0 and value == expected_days,
        "points_examined": lambda value: value == 21,
        "last_success": lambda value: 0 <= now - value <= 900,
    }.items():
        metric = metrics.get(name, {})
        values = metric.get("values", [])
        if metric.get("status") != "observed" or len(values) != 1:
            checks[name] = "unknown"
        elif not math.isfinite(values[0]):
            checks[name] = "unknown"
        else:
            checks[name] = "pass" if predicate(values[0]) else "fail"
    return checks


def command(argv):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=15, check=False)
        if result.returncode or len(result.stdout) > LIMIT:
            return None
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def backup_metadata(directory, now):
    try:
        files = [(p, p.stat()) for p in Path(directory).glob("sora_earth_*.dump")
                 if not p.is_symlink() and p.is_file()]
        if not files:
            return {"status": "fail", "reason": "no_local_dumps", "count": 0}
        path, stat = max(files, key=lambda item: item[1].st_mtime)
        age = now - stat.st_mtime
        return {"status": "pass" if 0 <= age <= 26 * 3600 and stat.st_size > 0 else "fail",
                "count": len(files), "latest_name": path.name,
                "age_seconds": round(age), "bytes": stat.st_size,
                "archive_integrity": "not_checked", "offsite_recovery": "not_checked"}
    except OSError:
        return {"status": "unknown", "reason": "metadata_unreadable"}


def http_status(url):
    try:
        with urlopen(url, timeout=15) as response:
            return response.status
    except Exception:
        return None


def collect():
    now = datetime.now(timezone.utc)
    expressions = {
        "app_up": 'up{job="sora-app"}',
        "scheduler_up": 'up{job="sora-scheduler"}',
        "gap_count": "sora_observation_coverage_gap_days{" + PAIR + "}",
        "days_examined": "sora_observation_coverage_days_examined{" + PAIR + "}",
        "points_examined": "sora_observation_coverage_points_examined{" + PAIR + "}",
        "last_success": "sora_observation_coverage_last_success_timestamp_seconds{" + PAIR + "}",
    }
    metrics = {name: query(expr) for name, expr in expressions.items()}
    return {
        "observed_at": now.isoformat(),
        "release_sha": command(["git", "-C", "/opt/sora_earth_ai_platform", "rev-parse", "HEAD"]),
        "health_http": http_status("https://sora-earth.online/health"),
        "ready_http": http_status("https://sora-earth.online/api/v1/ready"),
        "metrics": metrics,
        "checks": evaluate(metrics, now.timestamp()),
        "local_backup": backup_metadata("/var/backups/sora", now.timestamp()),
        "backup_units": command(["systemctl", "show", "sora-backup-local.timer",
                                  "sora-backup-local.service", "sora-backup-age.timer",
                                  "sora-backup-age.service", "sora-backup.timer",
                                  "-p", "Id", "-p", "LoadState", "-p", "ActiveState",
                                  "-p", "UnitFileState", "-p", "Result", "-p", "ExecMainStatus",
                                  "-p", "LastTriggerUSec", "-p", "NextElapseUSecRealtime"]),
        "notification_delivery": "not_verified",
        "restore_drill": "not_run_by_this_collector",
        "deployment_permission": "none",
    }


if __name__ == "__main__":
    json.dump(collect(), sys.stdout, ensure_ascii=False, indent=2)
    print()
