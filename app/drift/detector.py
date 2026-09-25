"""Drift detector: reads reference stats + live JSONL, returns per-feature report."""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.obs import request_log

from app.drift.metrics import (
    psi, ks, histogram, category_counts, status_from_psi,
)

REF_PATH = Path("output/ref_stats.json")

#: The directory the writer writes to, not a second spelling of its default.
#:
#: `app/obs/request_log.py` honours `SORA_REQUEST_LOG_DIR`; this module used to
#: hardcode the default. Measured: with the variable set and rows written to the
#: configured directory, the report read the old path, found nothing, and called
#: every feature "stable". Both spellings were equal until someone set the
#: variable, which is why `==` between them was a check that could not fail.
LOG_DIR = request_log.LOG_DIR

#: What a feature reports when the window held no observations at all.
#:
#: An empty live sample gives PSI exactly 0.0, and 0.0 is below the "stable"
#: threshold -- so "we measured nothing" arrived as "nothing is wrong", which is
#: what a dashboard and an alert read. The same distinction the closed loop
#: makes for the other drift check: a check that could not run is not "no
#: drift".
NOT_MEASURED = "not_measured"


def _load_live(window_hours: int = 24) -> list[dict]:
    if not LOG_DIR.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    rows: list[dict] = []
    # read last 2 daily files (covers window crossing midnight UTC)
    files = sorted(LOG_DIR.glob("*.jsonl"))[-2:]
    for f in files:
        for line in f.read_text().splitlines():
            try:
                r = json.loads(line)
                ts = datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
                if ts >= cutoff:
                    rows.append(r)
            except Exception:
                continue
    return rows


def _live_df(rows: list[dict]) -> dict[str, list]:
    if not rows:
        return {}
    out: dict[str, list] = {}
    for r in rows:
        for k, v in r.get("features", {}).items():
            out.setdefault(k, []).append(v)
        out.setdefault("__prob__", []).append(r["pred"]["prob"])
    return out


def report_features(window_hours: int = 24) -> dict[str, Any]:
    if not REF_PATH.exists():
        raise FileNotFoundError("run scripts/build_ref_stats.py first")
    ref = json.loads(REF_PATH.read_text())
    rows = _load_live(window_hours)
    live = _live_df(rows)

    out: list[dict] = []
    # numeric features
    for feat, stats in ref.get("numeric", {}).items():
        edges = np.array(stats["bin_edges"])
        ref_counts = np.array(stats["counts_ref"])
        vals = np.array(live.get(feat, []), dtype=float)
        live_counts = histogram(vals, edges) if len(vals) else np.zeros_like(ref_counts)
        psi_v = psi(ref_counts, live_counts) if live_counts.sum() else 0.0
        ks_stat, ks_p = ks(stats["sample_ref"], vals) if len(vals) else (0.0, 1.0)
        out.append({
            "feature": feat, "kind": "numeric",
            "psi": round(psi_v, 4), "ks_stat": round(ks_stat, 4),
            "ks_pvalue": round(ks_p, 6),
            "status": status_from_psi(psi_v) if len(vals) else NOT_MEASURED,
            "n_live": int(len(vals)),
        })
    # categorical
    for feat, freqs in ref.get("categorical", {}).items():
        cats = list(freqs.keys())
        ref_counts = np.array([int(freqs[c] * ref["n_samples"]) for c in cats])
        vals = live.get(feat, [])
        live_counts = category_counts(vals, cats)
        psi_v = psi(ref_counts, live_counts) if live_counts.sum() else 0.0
        out.append({
            "feature": feat, "kind": "categorical",
            "psi": round(psi_v, 4), "ks_stat": None, "ks_pvalue": None,
            "status": status_from_psi(psi_v) if len(vals) else NOT_MEASURED,
            "n_live": int(len(vals)),
        })
    return {
        "window_hours": window_hours,
        "n_live_total": len(rows),
        "ref_model_version": ref.get("model_version"),
        "features": out,
    }


def report_predictions(window_hours: int = 24) -> dict[str, Any]:
    if not REF_PATH.exists():
        raise FileNotFoundError("run scripts/build_ref_stats.py first")
    ref = json.loads(REF_PATH.read_text())
    pred_ref = ref["prediction"]
    edges = np.array(pred_ref["bin_edges"])
    ref_counts = np.array(pred_ref["counts_ref"])

    rows = _load_live(window_hours)
    probs = np.array([r["pred"]["prob"] for r in rows], dtype=float)
    live_counts = histogram(probs, edges) if len(probs) else np.zeros_like(ref_counts)
    psi_v = psi(ref_counts, live_counts) if live_counts.sum() else 0.0
    ks_stat, ks_p = ks(pred_ref.get("sample_ref", []), probs) if len(probs) else (0.0, 1.0)

    return {
        "window_hours": window_hours, "n_live": int(len(probs)),
        "psi": round(psi_v, 4), "ks_stat": round(ks_stat, 4),
        "ks_pvalue": round(ks_p, 6),
        "status": status_from_psi(psi_v) if len(probs) else NOT_MEASURED,
        "bin_edges": edges.tolist(),
        "counts_ref": ref_counts.tolist(),
        "counts_live": live_counts.tolist(),
    }
