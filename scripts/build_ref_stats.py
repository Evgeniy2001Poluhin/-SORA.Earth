"""Build reference distributions from a train set for drift detection.

Usage:
    python scripts/build_ref_stats.py --csv data/projects.csv
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

NUMERIC = ["budget", "co2_reduction", "social_impact", "duration_months"]
CATEGORICAL = ["category", "region"]
N_BINS = 10
N_SAMPLE = 2000


def _build_numeric(vals, n_bins):
    edges = np.unique(np.quantile(vals, np.linspace(0, 1, n_bins + 1)))
    if len(edges) < 3:
        edges = np.linspace(vals.min(), vals.max() + 1e-6, n_bins + 1)
    counts, _ = np.histogram(vals, bins=edges)
    rng = np.random.default_rng(42)
    sample = rng.choice(vals, size=min(N_SAMPLE, len(vals)), replace=False)
    return {
        "bin_edges": edges.tolist(),
        "counts_ref": counts.astype(int).tolist(),
        "mean": float(vals.mean()),
        "p50": float(np.median(vals)),
        "sample_ref": sample.tolist(),
    }


def main(csv_path, out_path, n_bins=N_BINS):
    df = pd.read_csv(csv_path)
    missing = set(NUMERIC + CATEGORICAL) - set(df.columns)
    assert not missing, f"missing cols: {missing}"

    numeric = {c: _build_numeric(df[c].dropna().values.astype(float), n_bins) for c in NUMERIC}
    categorical = {
        c: {str(k): float(v) for k, v in df[c].value_counts(normalize=True, dropna=False).to_dict().items()}
        for c in CATEGORICAL
    }

    import requests
    probs = []
    batch = df.sample(min(500, len(df)), random_state=42)
    for _, row in batch.iterrows():
        body = {c: (float(row[c]) if c in NUMERIC else str(row[c])) for c in NUMERIC + CATEGORICAL}
        try:
            r = requests.post("http://127.0.0.1:8000/api/v2/predict", json=body, timeout=5)
            if r.ok:
                probs.append(r.json()["success_probability"])
        except Exception:
            pass
    probs = np.array(probs) if probs else np.array([0.5])
    edges = np.linspace(0, 1, n_bins + 1)
    counts, _ = np.histogram(probs, bins=edges)

    model_version = "unknown"
    try:
        model_version = requests.get("http://127.0.0.1:8000/api/v2/model/version", timeout=3).json()["version"]
    except Exception:
        pass

    out = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model_version": str(model_version),
        "n_samples": int(len(df)),
        "n_bins": int(n_bins),
        "numeric": numeric,
        "categorical": categorical,
        "prediction": {
            "bin_edges": edges.tolist(),
            "counts_ref": counts.astype(int).tolist(),
            "sample_ref": probs.tolist()[:N_SAMPLE],
        },
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(out, indent=2))
    print(f"wrote {out_path}  n_samples={len(df)}  model=v{model_version}  n_preds={len(probs)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", default="output/ref_stats.json")
    ap.add_argument("--bins", type=int, default=N_BINS)
    a = ap.parse_args()
    main(a.csv, a.out, a.bins)
