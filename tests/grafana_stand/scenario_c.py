"""Portable scenario C schedule. Default/--plan is inert; --execute is opt-in.

Records observations, never declares the alert rules accepted. No provisioning,
container startup/deletion or accelerated timing is performed by this driver.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

HERE = Path(__file__).resolve().parent
BASELINE = ("app=ok", "drift_on=0", "rejected_on=0", "lat_ms=50", "lat_n=10",
            "gap=0", "gap_present=1", "ts_present=1", "ts_lag=0", "retrain_since=3600")
EVENTS = (
    (0, ("control", "app=ok", "drift_on=1", "rejected_on=1", "lat_ms=900",
         "lat_n=10", "gap=0", "gap_present=1", "ts_present=1", "ts_lag=1000",
         "retrain_since=1300000")),
    (900, ("control", "drift_on=0", "rejected_on=0", "lat_ms=50")),
    (3600, ("targets", "vanish")),
    (5100, ("targets", "restore")),
    (5100, ("control", *BASELINE)),
)
DURATION = 5700  # 85 minute drive, then 10 minutes observing recovery.


def plan():
    return {"schema": "local-stand-scenario-c-v1", "duration_seconds": DURATION,
            "events": [{"at_seconds": at, "command": command} for at, command in EVENTS],
            "runtime_acceptance": "not established by this driver"}


def run(perform, observe, emit, *, clock=time.monotonic, wall=time.time, sleep=time.sleep):
    """Injected clocks/transports make the exact schedule testable offline.

    A late wakeup aborts instead of silently compressing the scenario after
    sleep/suspension. Always attempt baseline restoration; retain failure evidence.
    """
    start, wall_start = clock(), wall()
    index, previous = 0, start
    try:
        while True:
            elapsed = clock() - start
            if clock() - previous > 30:
                raise RuntimeError("observation interval was missed")
            previous = clock()
            if abs((wall() - wall_start) - elapsed) > 30:
                raise RuntimeError("clock discontinuity")
            if index < len(EVENTS) and elapsed - EVENTS[index][0] > 30:
                raise RuntimeError("scheduled action was missed")
            while index < len(EVENTS) and elapsed >= EVENTS[index][0]:
                at, command = EVENTS[index]
                perform(command)
                emit({"kind": "action", "scheduled_seconds": at,
                      "elapsed_seconds": clock() - start, "command": command})
                index += 1
            emit({"kind": "observation", "elapsed_seconds": clock() - start,
                  "rules": observe()})
            if clock() - start >= DURATION:
                break
            next_event = EVENTS[index][0] if index < len(EVENTS) else DURATION
            sleep(max(0, min(10, next_event - (clock() - start))))
    except BaseException as error:
        emit({"kind": "aborted", "error_class": type(error).__name__})
        raise
    finally:
        failures = []
        for command in (("targets", "restore"), ("control", *BASELINE)):
            try:
                perform(command)
                emit({"kind": "restoration", "command": command, "ok": True})
            except BaseException as error:
                failures.append(type(error).__name__)
                emit({"kind": "restoration", "ok": False,
                      "error_class": type(error).__name__})
        if failures:
            raise RuntimeError("stand restoration incomplete")
    emit({"kind": "finished", "runtime_acceptance": "requires independent review"})


def command(arguments):
    return subprocess.run(["bash", str(HERE / "stand.sh"), *arguments],
                          check=True, capture_output=True, timeout=20).stdout


def sanitized_states(rows):
    # Never persist arbitrary API text, labels, error messages, paths or credentials.
    allowed_states = {"normal", "pending", "firing", "inactive", "alerting",
                      "Normal", "Pending", "Alerting", "NoData", "Error"}
    result = []
    for row in rows:
        uid = row.get("uid")
        if not isinstance(uid, str) or not uid.startswith("sora-") or not uid.replace("-", "").isalnum():
            raise ValueError("unexpected rule identity")
        state = row.get("state")
        health = row.get("health")
        if state not in allowed_states or health not in {"ok", "error", "nodata", "unknown"}:
            raise ValueError("unexpected rule state")
        alerts = [a.get("state") for a in row.get("alerts", [])]
        if any(a not in allowed_states for a in alerts):
            raise ValueError("unexpected alert state")
        result.append({"uid": uid, "state": state, "health": health,
                       "alerts": alerts, "has_error": bool(row.get("lastError"))})
    if not result:
        raise ValueError("no evaluator rules")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--plan", action="store_true")
    group.add_argument("--execute", metavar="NEW_OUTPUT_NAME")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps(plan(), indent=2))
        return
    import path_guard
    import check
    work, run_id = path_guard.session()
    command(("verify", "c"))
    sanitized_states(check.states())  # Readiness before changing target state.
    out = path_guard.new_output(work, args.execute)
    repo = HERE.parents[1]
    paths = [HERE / name for name in ("scenario_c.py", "stand.sh", "control.py",
                                    "metrics_server.py", "check.py", "path_guard.py",
                                    "stand-prometheus.yml")]
    paths += sorted((repo / "grafana/provisioning").rglob("*.yml"))
    manifest = {"plan": plan(), "run_id": run_id,
                "code_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
                "sha256": {str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}
    path_guard.write_new(out / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    # Exclusive, private output; do not overwrite a previous run or resume it.
    path_guard.write_new(out / "timeline.jsonl", "")
    with (out / "timeline.jsonl").open("a", encoding="utf-8") as stream:
        def emit(row):
            row["observed_at_utc"] = datetime.now(timezone.utc).isoformat()
            stream.write(json.dumps(row, sort_keys=True) + "\n")
            stream.flush()
        run(command, lambda: sanitized_states(check.states()), emit)
    print("scenario captured; independent acceptance review required")


if __name__ == "__main__":
    main()
