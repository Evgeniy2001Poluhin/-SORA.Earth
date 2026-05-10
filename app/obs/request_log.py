"""Sync JSONL request log for /api/v2/predict."""
from __future__ import annotations
import json, os, sys, traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

LOG_DIR = Path(os.getenv("SORA_REQUEST_LOG_DIR", "output/request_log"))
ENABLED = os.getenv("SORA_REQUEST_LOG", "0") == "1"
DEBUG = os.getenv("SORA_REQUEST_LOG_DEBUG", "0") == "1"
LOG_SCHEMA_VERSION = 1


def _today_path() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR / f"{datetime.now(timezone.utc):%Y-%m-%d}.jsonl"


def _append(line: str) -> None:
    with _today_path().open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def log_prediction(
    features: Mapping[str, Any],
    probability: float,
    label: int,
    latency_ms: float,
    model_version: str,
    model_alias: str = "champion",
) -> None:
    """Blocking append. FastAPI sync handlers run in threadpool — safe."""
    if not ENABLED:
        return
    try:
        record = {
            "v": LOG_SCHEMA_VERSION,
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "model": {"alias": model_alias, "version": model_version},
            "features": dict(features),
            "pred": {"prob": round(float(probability), 6), "label": int(label)},
            "latency_ms": round(float(latency_ms), 2),
        }
        _append(json.dumps(record, separators=(",", ":"), ensure_ascii=False))
    except Exception:
        if DEBUG:
            traceback.print_exc(file=sys.stderr)
