"""Local candidate: at most one forecast request per step; no service or scoring.

Unix lock + durable quota journal, with injected transport/clock for offline tests.
Caller-approved execution must use one shared ledger; this is not a sandbox
against the same OS user deleting state, creating other ledgers or extra clients.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta
import fcntl
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit
import uuid

from app.services.environmental.regions import OPENMETEO_POINTS_V1

from .replay_cache_plan import build_plan, _request
from .replay_p0 import canonical, parse_json, parse_payload

MAX_STATE = 2_097_152
MAX_RAW = 262_144
SHA = re.compile(r"[0-9a-f]{64}\Z")


class CacheRefusal(ValueError):
    pass


TRANSPORT_ERROR_CLASSES = frozenset({
    "HTTPError", "URLError", "TimeoutError", "SSLError", "RemoteDisconnected",
    "ConnectionResetError", "ReplayRefusal", "ImportError", "ModuleNotFoundError",
    "ValueError", "OSError", "JSONDecodeError",
})


class TransportFailure(CacheRefusal):
    def __init__(self, status=None, *, error_class=None, returncode=None):
        super().__init__("bounded public transport failed")
        self.code = status
        self.child_error_class = (
            error_class if isinstance(error_class, str) and error_class in TRANSPORT_ERROR_CLASSES else None
        )
        self.child_returncode = (
            returncode if type(returncode) is int and -255 <= returncode <= 255 else None
        )


def bounded_fetch(url, timeout, limit):
    """One child, one allowlisted GET; hard deadline includes imports/read time."""
    need(type(limit) is int and 0 < limit <= MAX_RAW and
         type(timeout) is int and 0 < timeout <= 40, "transport limits refused")
    child = """import json,sys
sys.path.insert(0,sys.argv[1])
try:
    from app.services.forecasting.replay_p0 import _fetch
    raw=_fetch(sys.argv[2],int(sys.argv[3]),int(sys.argv[4]))
    sys.stdout.buffer.write(raw)
except Exception as exc:
    code=getattr(exc,'code',None)
    sys.stderr.write(json.dumps({'error_class':type(exc).__name__,'code':code if type(code) is int else None}))
    sys.exit(2)
"""
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", child, str(Path(__file__).resolve().parents[3]),
             url, str(timeout), str(limit)], capture_output=True, timeout=45,
            env={"PATH": os.defpath, "TZ": "UTC"},
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("public transport hard deadline") from exc
    need(len(result.stdout) <= limit, "transport output byte limit")
    if result.returncode:
        status, child_class = None, None
        if len(result.stderr) <= 4096:
            try:
                error = parse_json(result.stderr)
                candidate = error.get("code") if isinstance(error, dict) else None
                if isinstance(error, dict):
                    child_class = error.get("error_class")
                if type(candidate) is int and 100 <= candidate <= 599:
                    status = candidate
            except ValueError:
                pass
        raise TransportFailure(status, error_class=child_class, returncode=result.returncode)
    return result.stdout


def need(condition, reason):
    if not condition:
        raise CacheRefusal(reason)


def utc(value):
    need(isinstance(value, datetime) and value.utcoffset() == timedelta(0), "explicit UTC required")
    return value


def stamp(value):
    try:
        return utc(datetime.fromisoformat(value))
    except (TypeError, ValueError) as exc:
        raise CacheRefusal("invalid ledger timestamp") from exc


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_regular(path, limit):
    need(path.is_file() and not path.is_symlink(), "expected regular owned artifact")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    need(len(raw) <= limit, "artifact byte limit")
    return raw


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_new(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync_dir(path.parent)


def save_state(root, state):
    raw = canonical(state)
    need(len(raw) <= MAX_STATE, "state byte limit")
    target = root / "state.json"
    need(not target.is_symlink(), "state symlink refused")
    temporary = root / ("state-" + uuid.uuid4().hex + ".tmp")
    write_new(temporary, raw)
    os.replace(temporary, target)
    sync_dir(root)


def initialize(root: Path, plan_raw: bytes, expected_pin: str, *, prior_units: int,
               now: datetime, code_commit: str):
    """Exclusive initialization only; never reset or replace an existing ledger."""
    utc(now)
    need(isinstance(plan_raw, bytes) and len(plan_raw) <= 1_048_576, "plan byte limit")
    need(type(prior_units) is int and 0 <= prior_units <= 1_000_000, "invalid prior reservation")
    need(isinstance(expected_pin, str) and SHA.fullmatch(expected_pin), "plan pin required")
    need(digest(plan_raw) == expected_pin and parse_json(plan_raw) == build_plan(), "unreviewed plan")
    need(isinstance(code_commit, str) and re.fullmatch(r"[0-9a-f]{40}", code_commit), "code commit required")
    need(root.parent.is_dir() and not root.exists() and not root.is_symlink(), "new cache directory required")
    root.mkdir(mode=0o700)
    (root / "raw").mkdir(mode=0o700)
    write_new(root / "plan.json", plan_raw)
    write_new(root / "worker.lock", b"")
    state = {"schema": 1, "plan_sha256": expected_pin, "code_commit": code_commit,
             "last_seen": now.isoformat(), "events": [{"at": now.isoformat(), "weight": prior_units,
             "kind": "prior_allowance"}], "completed": {}, "inflight": None, "paused": None}
    save_state(root, state)
    sync_dir(root.parent)


class CacheWorker:
    def __init__(self, root: Path, recovery_pin=None, continuation_pin=None):
        self.root = root
        self.recovery_pin = recovery_pin
        self.continuation_pin = continuation_pin

    def recovery_policy(self, state, now, requests):
        if state.get("calendar_continuation"):
            need(self.continuation_pin is not None, "explicit continuation pin required")
            from .replay_cache_continuation import validate_active
            return validate_active(self.root, state, self.continuation_pin,
                                   self.recovery_pin, now, requests)
        if not state.get("manual_recovery"):
            return None
        need(self.recovery_pin is not None, "explicit recovery amendment pin required")
        from .replay_cache_recovery import validate_active
        return validate_active(self.root, state, self.recovery_pin, now, requests)

    @contextmanager
    def locked(self, now):
        utc(now)
        need(self.root.is_dir() and not self.root.is_symlink(), "cache root refused")
        raw_dir = self.root / "raw"
        need(raw_dir.is_dir() and not raw_dir.is_symlink(), "raw directory refused")
        fd = os.open(self.root / "worker.lock", os.O_RDWR | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CacheRefusal("worker lock busy") from exc
            state = parse_json(read_regular(self.root / "state.json", MAX_STATE))
            plan_raw = read_regular(self.root / "plan.json", 1_048_576)
            need(digest(plan_raw) == state["plan_sha256"], "plan pin mismatch")
            plan = parse_json(plan_raw)
            need(plan == build_plan(), "plan differs from reviewed calendar")
            need(type(state["schema"]) is int and state["schema"] == 1 and
                 now >= stamp(state["last_seen"]), "clock rollback or schema mismatch")
            requests = {r["issue_date"]: r for r in plan["requests"]}
            requests.update({r["issue_date"]: _request(date.fromisoformat(r["issue_date"]))
                             for r in plan["reuse_candidates"]})
            need(isinstance(state["completed"], dict), "invalid completed journal")
            for day, receipt in state["completed"].items():
                need(day in requests, "undeclared completion")
                raw = read_regular(raw_dir / requests[day]["file"], MAX_RAW)
                need(digest(raw) == receipt["sha256"] and len(raw) == receipt["bytes"], "completed raw pin mismatch")
            for event in state["events"]:
                need(type(event["weight"]) is int and event["weight"] >= 0 and
                     stamp(event["at"]) <= now, "invalid quota event")
                need(event["kind"] in ("prior_allowance", "attempt"), "unknown quota event")
                if event["kind"] == "attempt":
                    need(event["weight"] == 21 and event["day"] in requests, "invalid attempt reservation")
            attempt_days = [e["day"] for e in state["events"] if e["kind"] == "attempt"]
            recovery = self.recovery_policy(state, now, requests)
            if recovery is None:
                need(len(attempt_days) == len(set(attempt_days)), "duplicate attempt reservation")
            need(sum(e["kind"] == "prior_allowance" for e in state["events"]) == 1, "missing or duplicate quota seed")
            if state["inflight"]:
                need(isinstance(state["inflight"], dict) and state["inflight"].get("day") in requests,
                     "invalid in-flight record")
                if state["inflight"]["source"]["kind"] == "reserved_http_attempt":
                    need(state["inflight"]["day"] in attempt_days, "missing in-flight quota reservation")
            for day, receipt in state["completed"].items():
                if receipt["source"]["kind"] == "reserved_http_attempt":
                    need(day in attempt_days, "missing completed quota reservation")
            state["last_seen"] = now.isoformat()
            yield state, plan, requests
        finally:
            os.close(fd)

    def validate(self, raw, request):
        plan = build_plan()
        protocol = {"points": [{"code": p.code, "lat": p.lat, "lon": p.lon}
                                for p in OPENMETEO_POINTS_V1],
                    "forecast_days": 8, "forecast_min_hours": 24}
        parsed = parse_payload(raw, protocol, {"kind": "forecast", "origin": request["origin"]})
        day = date.fromisoformat(request["issue_date"])
        targets = {day + timedelta(days=h) for h in range(1, 8)}
        need(len(parsed) == plan["point_count"] and
             all(targets <= set(days) <= targets | {day} for days in parsed.values()), "forecast target grid differs")

    def complete(self, state, request, raw, *, source):
        need(len(raw) <= MAX_RAW, "response byte limit")
        need(state["inflight"] and digest(raw) == state["inflight"].get("response_sha256"),
             "in-flight response pin mismatch")
        self.validate(raw, request)
        state["completed"][request["issue_date"]] = {
            "sha256": digest(raw), "bytes": len(raw), "source": source,
            "ledger_at": state["last_seen"], "historical_publication": "not attested"}
        state["inflight"] = None
        save_state(self.root, state)

    def import_one(self, day: str, source: Path, manifest_pin: str, *, now: datetime):
        """The independent manifest pin is required, never computed as its own trust anchor."""
        with self.locked(now) as (state, _, requests):
            need(not state["paused"] and not state["inflight"], "paused or uncertain ledger")
            need(day in requests, "undeclared import date")
            # Recovery's ordered attempt budget permits HTTP only for new dates.
            # Even an in-scope import could otherwise skip a required attempt.
            need(not state.get("manual_recovery") or day in state["completed"],
                 "new imports forbidden during active recovery")
            need(isinstance(manifest_pin, str) and SHA.fullmatch(manifest_pin), "source manifest pin required")
            manifest_raw = read_regular(source / "manifest.json", 1_048_576)
            need(digest(manifest_raw) == manifest_pin, "source manifest pin mismatch")
            manifest = parse_json(manifest_raw)
            need(manifest.get("complete") is True, "incomplete import manifest")
            request = requests[day]
            url = urlsplit(request["url"])
            def same_url(entry):
                candidate = urlsplit(entry.get("url", ""))
                return (candidate.scheme, candidate.netloc, candidate.path, parse_qs(candidate.query)) == (
                    url.scheme, url.netloc, url.path, parse_qs(url.query))
            matching = [entry for entry in manifest["entries"] if same_url(entry)]
            need(len(matching) == 1, "ambiguous source request")
            entry = matching[0]
            name = entry["file"]
            need(isinstance(name, str) and re.fullmatch(r"[a-zA-Z0-9_-]+\.json", name), "source filename refused")
            raw = read_regular(source / name, MAX_RAW)
            need(digest(raw) == entry["sha256"] and len(raw) == entry["bytes"], "source raw pin mismatch")
            target = self.root / "raw" / request["file"]
            if day in state["completed"]:
                need(state["completed"][day]["sha256"] == digest(raw), "conflicting import")
                return {"status": "already_present", "day": day}
            need(not target.exists() and not target.is_symlink(), "unreceipted raw requires review")
            self.validate(raw, request)
            state["inflight"] = {"day": day, "source": {"kind": "import", "manifest_sha256": manifest_pin},
                                 "response_sha256": digest(raw)}
            save_state(self.root, state)
            write_new(target, raw)
            self.complete(state, request, raw, source={"kind": "import", "manifest_sha256": manifest_pin})
            return {"status": "imported", "day": day}

    def wait_until(self, state, now):
        blockers = []
        for seconds, cap in ((60, 400), (3600, 4000), (86400, 7000)):
            active = sorted((stamp(e["at"]), e["weight"]) for e in state["events"]
                            if now - stamp(e["at"]) < timedelta(seconds=seconds))
            used = sum(weight for _, weight in active)
            for moment, weight in active:
                if used + 21 <= cap:
                    break
                blockers.append(moment + timedelta(seconds=seconds))
                used -= weight
        attempts = [stamp(e["at"]) for e in state["events"] if e["kind"] == "attempt"]
        if attempts:
            blockers.append(max(attempts) + timedelta(seconds=4))
        return max([now, *blockers])

    def step(self, *, now: datetime, fetch, code_commit: str, clock=None):
        """No sleeping or automatic retries. Transport is explicit; tests provide a fake."""
        with self.locked(now) as (state, plan, requests):
            recovery = self.recovery_policy(state, now, requests)
            expected_code = recovery["execution_commit"] if recovery else state["code_commit"]
            need(code_commit == expected_code, "execution code differs from initialized/amended commit")
            if state["paused"]:
                return {"status": "paused", "reason": state["paused"]}
            if state["inflight"]:
                request = requests[state["inflight"]["day"]]
                target = self.root / "raw" / request["file"]
                if not target.exists():
                    state["paused"] = "uncertain_attempt_without_raw; review required, no retry"
                    save_state(self.root, state)
                    return {"status": "paused", "reason": state["paused"]}
                try:
                    raw = read_regular(target, MAX_RAW)
                    source = {**state["inflight"]["source"], "recovered_without_http": True}
                    self.complete(state, request, raw, source=source)
                except Exception as exc:
                    state["paused"] = "recovery_failed:" + type(exc).__name__
                    save_state(self.root, state)
                    return {"status": "paused", "reason": state["paused"]}
                return {"status": "recovered_without_http", "day": request["issue_date"]}
            required = {r["issue_date"] for r in [*plan["reuse_candidates"], *plan["probe_requests"]]}
            missing = sorted(required - state["completed"].keys())
            if missing:
                return {"status": "imports_required", "dates": missing}
            pending = [r for r in plan["requests"] if r["issue_date"] not in state["completed"]]
            if recovery:
                pending = [r for r in pending if r["issue_date"] in recovery["allowed_missing_dates"]]
                if not pending:
                    return {"status": recovery.get("completion_status", "first_slice_complete"),
                            "count": len(state["completed"])}
                need(len([e for e in state["events"] if e["kind"] == "attempt"]) <
                     recovery["max_total_attempts"], "amended attempt cap reached")
            if not pending:
                return {"status": "all_downloaded_pending_final_audit", "count": len(state["completed"])}
            if clock:
                current = utc(clock())
                need(current >= now, "clock rollback before reservation")
                now = current
                state["last_seen"] = now.isoformat()
            allowed = self.wait_until(state, now)
            if allowed > now:
                save_state(self.root, state)
                return {"status": "quota_wait", "not_before": allowed.isoformat()}
            request = pending[0]
            target = self.root / "raw" / request["file"]
            need(not target.exists() and not target.is_symlink(), "unreceipted response requires review")
            state["events"].append({"at": now.isoformat(), "weight": 21, "kind": "attempt", "day": request["issue_date"]})
            if recovery and request["issue_date"] == recovery["failed_day"]:
                state["events"][-1].update(attempt_ordinal=2,
                                          recovery_amendment_sha256=self.recovery_pin)
            if state.get("calendar_continuation"):
                state["events"][-1]["calendar_continuation_sha256"] = self.continuation_pin
            state["inflight"] = {"day": request["issue_date"], "source": {"kind": "reserved_http_attempt"}}
            save_state(self.root, state)  # Must finish durably BEFORE transport.
            completion_recorded = False
            try:
                raw = fetch(request["url"], 40, MAX_RAW)
                need(isinstance(raw, bytes) and len(raw) <= MAX_RAW, "response byte limit/type")
                finished = utc(clock()) if clock else now
                need(finished >= now, "clock rollback during request")
                # Keep the immutable reservation start, but charge rolling windows
                # until response completion, conservatively including transport latency.
                state["events"][-1]["reserved_at"] = now.isoformat()
                state["events"][-1]["at"] = finished.isoformat()
                state["last_seen"] = finished.isoformat()
                completion_recorded = True
                state["inflight"]["response_sha256"] = digest(raw)
                save_state(self.root, state)  # Pin received bytes before writing/recovery.
                write_new(target, raw)
                self.complete(state, request, raw, source={"kind": "reserved_http_attempt"})
            except Exception as exc:
                if not completion_recorded:
                    try:
                        failed_at = utc(clock()) if clock else now
                        need(failed_at >= now, "clock rollback during failed request")
                    except Exception:
                        # Unknown completion must not become an eligible manual retry.
                        state["paused"] = "failure_completion_clock_uncertain; review required"
                        save_state(self.root, state)
                        return {"status": "paused", "reason": state["paused"]}
                    state["events"][-1]["reserved_at"] = now.isoformat()
                    state["events"][-1]["at"] = failed_at.isoformat()
                    state["last_seen"] = failed_at.isoformat()
                state["paused"] = "request_failed:" + type(exc).__name__
                if isinstance(exc, TransportFailure):
                    state["failure_diagnostic"] = {
                        "child_error_class": exc.child_error_class,
                        "child_returncode": exc.child_returncode,
                    }
                if type(getattr(exc, "code", None)) is int:
                    state["paused"] += ":" + str(exc.code)
                save_state(self.root, state)
                return {"status": "paused", "reason": state["paused"]}
            return {"status": "downloaded", "day": request["issue_date"], "sha256": digest(raw)}

    def audit(self, *, now: datetime):
        with self.locked(now) as (state, _, requests):
            for day, receipt in state["completed"].items():
                raw = read_regular(self.root / "raw" / requests[day]["file"], MAX_RAW)
                self.validate(raw, requests[day])
            return {"status": "audit", "completed": len(state["completed"]),
                    "expected": len(requests), "complete_grid": len(state["completed"]) == len(requests),
                    "paused": state["paused"], "inflight": state["inflight"],
                    "ready_for_review": len(state["completed"]) == len(requests) and
                    state["paused"] is None and state["inflight"] is None,
                    "real_calibration_or_scoring": False}
