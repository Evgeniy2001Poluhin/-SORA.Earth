"""Bounded exploratory weather replay. No database, promotion or live M3 writes.

Only public API snapshots are acquired; the old private archive is not a source.
Verification uses a retrieved ERA5 vintage, including training prefixes, so this
is explicitly pseudo-real-time, never then-available operational performance.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import numpy as np
import pandas as pd

from .baselines import ClimatologyForecast, default_baselines
from .daily_target import daily_observations
from .evaluation import EvaluationReport, evaluate_rolling_origin

ROOT = Path(__file__).resolve().parents[3]
PROTOCOL = "docs/research/M3_REPLAY_P0.json"
LABEL = "pseudo-real-time reconstructed vintage; not a real-time claim; not live M3"
_HOUR = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:00\Z")


class ReplayRefusal(ValueError):
    pass


def need(condition, reason):
    if not condition:
        raise ReplayRefusal(reason)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode()


def _object(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def parse_json(raw: bytes):
    try:
        return json.loads(raw, object_pairs_hook=_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ReplayRefusal("nonfinite JSON")))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ReplayRefusal("invalid or ambiguous JSON") from exc


def frozen_protocol():
    """Refuse acquisition/scoring from uncommitted source or protocol bytes."""
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT, timeout=10)
    try:
        need(not git("status", "--porcelain", "--untracked-files=no").strip(),
             "tracked working tree must be clean")
        for name in (PROTOCOL, "app/services/forecasting/replay_p0.py",
                     "scripts/run_forecast_replay_p0.py"):
            need((ROOT / name).read_bytes() == git("show", f"HEAD:{name}"),
                 "protocol and executed source must be committed")
        raw = (ROOT / PROTOCOL).read_bytes()
        return parse_json(raw), {
            "protocol_sha256": digest(raw),
            "protocol_commit": git("log", "-1", "--format=%H", "--", PROTOCOL).decode().strip(),
            "code_commit": git("rev-parse", "HEAD").decode().strip(),
        }
    except (subprocess.SubprocessError, OSError) as exc:
        raise ReplayRefusal("cannot establish committed provenance") from exc


def request_plan(protocol):
    params = dict(protocol["shared_params"])
    params.update(latitude=",".join(str(p["lat"]) for p in protocol["points"]),
                  longitude=",".join(str(p["lon"]) for p in protocol["points"]))
    acquisition = protocol["acquisition"]
    rows = [{"file": "verification.json", "kind": "verification", "origin": None,
             "url": acquisition["verification_endpoint"] + "?" + urlencode({
                 **params, "models": protocol["verification_model"],
                 "start_date": protocol["training_start"], "end_date": protocol["verification_end"],
             })}]
    for index, origin in enumerate(protocol["origins"]):
        day = datetime.fromisoformat(origin).date().isoformat()
        rows.append({"file": f"forecast-{index:02d}.json", "kind": "forecast", "origin": origin,
                     "url": acquisition["forecast_endpoint"] + "?" + urlencode({
                         **params, "models": protocol["forecast_model"],
                         "run": day + "T00:00", "forecast_days": protocol["forecast_days"],
                     })})
    days = (date.fromisoformat(protocol["verification_end"]) -
            date.fromisoformat(protocol["training_start"])).days + 1
    weighted = len(protocol["points"]) * (days + len(protocol["origins"]) * protocol["forecast_days"]) / 14
    need(weighted <= acquisition["max_estimated_weighted_calls"], "estimated API budget exceeded")
    return rows, weighted


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _fetch(url, timeout, byte_limit):
    # Fixed public hosts from the committed protocol. No credentials/proxy/redirects.
    parsed = urlsplit(url)
    need(parsed.scheme == "https" and parsed.netloc in
         ("archive-api.open-meteo.com", "single-runs-api.open-meteo.com") and
         parsed.path in ("/v1/archive", "/v1/forecast"), "unapproved public endpoint")
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    with opener.open(Request(url, headers={"User-Agent": "SORA-offline-prototype-replay/1.0"}),
                     timeout=timeout) as response:
        need(response.status == 200, "provider did not return HTTP 200")
        raw = response.read(byte_limit + 1)
    need(len(raw) <= byte_limit, "provider response exceeds byte budget")
    return raw


def _write_new(path: Path, raw: bytes):
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def acquire_snapshot(out: Path, protocol, provenance, fetch=_fetch, progress=None):
    """Exclusive new directory; failures leave files for inspection, never reuse."""
    requests, weighted = request_plan(protocol)
    limits = protocol["acquisition"]
    need(len(requests) * (limits["retries_per_request"] + 1) <= limits["max_http_attempts"],
         "HTTP attempt budget is inconsistent")
    need(out.parent.is_dir() and not out.exists(), "snapshot needs an existing parent and new directory")
    out.mkdir(mode=0o700)
    entries, total, attempts = [], 0, 0
    for index, item in enumerate(requests):
        start = datetime.now(timezone.utc).isoformat()
        for retry in range(limits["retries_per_request"] + 1):
            attempts += 1
            try:
                raw = fetch(item["url"], limits["timeout_seconds"], limits["max_bytes_per_response"])
                break
            except HTTPError as exc:
                if exc.code not in (408, 429, 500, 502, 503, 504) or retry == limits["retries_per_request"]:
                    raise ReplayRefusal(f"provider HTTP {exc.code}; snapshot remains incomplete") from exc
                time.sleep(1)
            except (URLError, TimeoutError, OSError) as exc:
                if retry == limits["retries_per_request"]:
                    raise ReplayRefusal("provider connection failed; snapshot remains incomplete") from exc
                time.sleep(1)
        total += len(raw)
        need(len(raw) <= limits["max_bytes_per_response"], "response byte budget exceeded")
        need(total <= limits["max_bytes_total"], "total snapshot byte budget exceeded")
        # Refuse schema/coverage defects immediately without inspecting any error metric.
        parse_payload(raw, protocol, item)
        _write_new(out / item["file"], raw)
        entries.append({**item, "sha256": digest(raw), "bytes": len(raw),
                        "requested_at": start, "retrieved_at": datetime.now(timezone.utc).isoformat()})
        if progress:
            progress(f"received {index+1}/{len(requests)} {item['file']} ({len(raw)} bytes)")
    manifest = {"schema_version": 1, "label": LABEL, "protocol": protocol,
                "provenance": provenance, "entries": entries, "http_attempts": attempts,
                "estimated_weighted_calls": weighted, "bytes": total,
                "source_authenticity": "retrieved vintage only; historical publication not attested",
                "complete": True}
    _write_new(out / "manifest.json", canonical(manifest))
    fd = os.open(out, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return manifest


def _bounds(protocol, item):
    if item["kind"] == "verification":
        return date.fromisoformat(protocol["training_start"]), date.fromisoformat(protocol["verification_end"])
    origin = datetime.fromisoformat(item["origin"]).date()
    return origin, origin + timedelta(days=protocol["forecast_days"] - 1)


def parse_payload(raw: bytes, protocol, item):
    """Check all declared points and all UTC days, including entirely absent days."""
    payload = parse_json(raw)
    points = protocol["points"]
    if isinstance(payload, dict) and len(points) == 1:
        payload = [payload]
    need(isinstance(payload, list) and len(payload) == len(points), "provider point count differs")
    start, end = _bounds(protocol, item)
    days = (end - start).days + 1
    floor = protocol["observation_min_hours"] if item["kind"] == "verification" else protocol["forecast_min_hours"]
    result = {}
    for point, response in zip(points, payload):
        need(isinstance(response, dict), "provider point is not an object")
        for coordinate in ("latitude", "longitude"):
            value = response.get(coordinate)
            requested = point["lat" if coordinate == "latitude" else "lon"]
            need(type(value) in (int, float) and math.isfinite(value) and abs(value-requested) <= 0.3,
                 "provider point order/location differs beyond grid tolerance")
        need(response.get("timezone") in ("GMT", "UTC") and
             type(response.get("utc_offset_seconds")) is int and response["utc_offset_seconds"] == 0,
             "provider timezone must explicitly be UTC")
        need(response.get("hourly_units") == {"time": "iso8601", "temperature_2m": "°C"},
             "provider units differ from Celsius/ISO8601")
        hourly = response.get("hourly")
        need(isinstance(hourly, dict), "missing hourly data")
        times, values = hourly.get("time"), hourly.get("temperature_2m")
        need(isinstance(times, list) and isinstance(values, list) and len(times) == len(values),
             "hourly arrays have different lengths")
        seen, rows, counts = set(), [], {}
        for stamp, value in zip(times, values):
            need(isinstance(stamp, str) and _HOUR.fullmatch(stamp), "invalid naive-UTC hourly timestamp")
            try:
                moment = datetime.strptime(stamp, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
            except ValueError as exc:
                raise ReplayRefusal("invalid calendar timestamp") from exc
            need(start <= moment.date() <= end, "timestamp outside frozen request bounds")
            need(moment not in seen, "duplicate point/hour")
            seen.add(moment)
            if value is None:
                continue
            need(type(value) in (int, float) and math.isfinite(value), "nonfinite/nonnumeric hourly value")
            rows.append((point["code"], moment, float(value)))
            counts[moment.date()] = counts.get(moment.date(), 0) + 1
        for offset in range(days):
            if item["kind"] == "forecast" and offset == 0:
                # The run's origin day is downloaded but never scored.
                continue
            need(counts.get(start + timedelta(days=offset), 0) >= floor,
                 "declared point/day lacks required distinct hours")
        observed = daily_observations(rows)
        result[point["code"]] = {obs.period_end: obs.value for obs in observed}
    return result


def read_snapshot(directory: Path, protocol, expected_manifest_sha: str):
    """Read each raw file once: the hash authenticates exactly the bytes parsed."""
    with (directory / "manifest.json").open("rb") as stream:
        manifest_raw = stream.read(1_000_001)
    need(len(manifest_raw) <= 1_000_000, "manifest exceeds byte limit")
    need(digest(manifest_raw) == expected_manifest_sha, "external manifest pin mismatch")
    manifest = parse_json(manifest_raw)
    need(isinstance(manifest, dict) and manifest.get("complete") is True, "incomplete snapshot")
    need(manifest.get("protocol") == protocol, "snapshot protocol differs")
    requests, _ = request_plan(protocol)
    entries = manifest.get("entries")
    need(isinstance(entries, list) and len(entries) == len(requests), "snapshot entry count differs")
    parsed, total = {}, 0
    for expected, entry in zip(requests, entries):
        need(isinstance(entry, dict) and all(entry.get(k) == v for k, v in expected.items()),
             "snapshot request/file/order differs")
        path = directory / expected["file"]
        need(path.is_file() and not path.is_symlink(), "snapshot entry must be a regular file")
        with path.open("rb") as stream:
            raw = stream.read(protocol["acquisition"]["max_bytes_per_response"] + 1)
        total += len(raw)
        need(len(raw) <= protocol["acquisition"]["max_bytes_per_response"] and
             total <= protocol["acquisition"]["max_bytes_total"], "snapshot byte budget exceeded")
        need(digest(raw) == entry.get("sha256") and len(raw) == entry.get("bytes"), "snapshot pin mismatch")
        parsed[expected["file"]] = parse_payload(raw, protocol, expected)
    return manifest, digest(manifest_raw), parsed


def evaluate_snapshot(directory: Path, protocol, provenance, expected_manifest_sha: str):
    manifest, manifest_sha, series = read_snapshot(directory, protocol, expected_manifest_sha)
    need(manifest["provenance"]["protocol_sha256"] == provenance["protocol_sha256"], "protocol pin mismatch")
    reports = {}
    for point in protocol["points"]:
        code = point["code"]
        actual = series["verification.json"][code]
        methods = {"API_IFS_uncalibrated": None,
                   **{model.model_name: model for model in default_baselines()},
                   "Climatology(±3d)": ClimatologyForecast()}
        need(set(methods) == set(protocol["methods"]), "comparison methods differ from frozen protocol")
        reports[code] = {name: EvaluationReport(name, protocol["horizon"]) for name in methods}
        for index, origin in enumerate(protocol["origins"]):
            origin_day = datetime.fromisoformat(origin).date()
            train_days = sorted(day for day in actual if day <= origin_day)
            target_days = [origin_day + timedelta(days=i+1) for i in range(protocol["horizon"])]
            frame_days = train_days + target_days
            frame = pd.DataFrame({"ds": pd.to_datetime(frame_days), "y": [actual[day] for day in frame_days]})
            nwp = series[f"forecast-{index:02d}.json"][code]
            for name, model in methods.items():
                def fit_predict(train, horizon, model=model):
                    need(train["ds"].max().date() == origin_day, "training prefix ends outside origin")
                    need(len(train) == len(train_days), "training prefix length differs")
                    if model is None:
                        return [nwp[day] for day in target_days]
                    model.fit(train, "y")
                    prediction = model.predict(horizon)
                    need(prediction.dates == [day.isoformat() for day in target_days], "prediction dates differ")
                    return prediction.yhat
                report = evaluate_rolling_origin(fit_predict, frame, "y", protocol["horizon"],
                                                 len(train_days), 1, model_name=name)
                window = report.windows[0]
                window.window = index
                need(all(math.isfinite(getattr(window, metric)) for metric in ("mae", "rmse", "mase", "bias")),
                     "nonfinite evaluation metric")
                reports[code][name].windows.append(window)
    by_model = {}
    for name in protocol["methods"]:
        point_metrics = {code: {m: report[name].mean(m) for m in ("mae", "rmse", "mase", "bias")}
                         for code, report in reports.items()}
        by_model[name] = {
            "macro": {metric: float(np.mean([p[metric] for p in point_metrics.values()]))
                      for metric in ("mae", "rmse", "mase", "bias")},
            "by_point": point_metrics,
            "worst_point_mae": max(point_metrics, key=lambda code: point_metrics[code]["mae"]),
            "windows": {code: [dict(window=window.window, mae=window.mae, rmse=window.rmse,
                                    mase=window.mase, bias=window.bias) for window in report[name].windows]
                        for code, report in reports.items()},
        }
    return {"protocol_id": protocol["id"], "label": LABEL, "provenance": provenance,
            "manifest_sha256": manifest_sha, "acquisition_provenance": manifest["provenance"],
            "points": len(protocol["points"]), "windows_per_point": len(protocol["origins"]),
            "days_per_window": protocol["horizon"], "origins": protocol["origins"],
            "methods": by_model, "verdict": "exploratory_only_no_confirmatory_claim",
            "promotion_allowed": False,
            "limitations": ["verification is retrieved ERA5, not thermometers",
                            "past training labels are revised-vintage data, not known-at-origin records",
                            "provider IFS cycle/historical publication not attested",
                            "12 sampled weeks of one year, not 12 consecutive live M3 windows",
                            "no uncertainty coverage or significance claim"],
            "generated_at": datetime.now(timezone.utc).isoformat()}
