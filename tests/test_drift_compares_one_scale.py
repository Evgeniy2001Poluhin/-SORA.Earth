"""Drift detection compares social_impact on one scale (the model's).

Measured defect (2026-09-25): Since the owner's decision (option B, 2026-09-25),
clients and the interface send social_impact on 0-10, and the models were
trained on data/projects.csv where it is 0-100. The drift checks compared what
clients sent with the training data without converting.

Measured with scipy's ks_2samp against data/projects.csv: interface-like values
(7-9, or uniform 1-10) give KS = 0.995, p ≈ 1e-116, so drift is declared for
any real traffic. A control drawn from the training data itself gives p = 0.82
(no drift). So the drift verdict on social_impact was a scale artefact.
"""
import os
import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


def test_compute_drift_realistic_traffic():
    """compute_drift does not flag social_impact drift for typical API values.

    Insert N prediction log rows with social_impact on the API scale (0-10),
    drawn from the training data's social_impact / 10. Assert that
    social_impact is not flagged as drift.

    On the base branch (before conversion), this test FAILS: the KS test
    compares API-scale values (0-10) with training-scale values (0-100) and
    declares drift.
    """
    from app.database import SessionLocal, PredictionLog
    from app.api.drift import compute_drift, MIN_WINDOW_ROWS
    from app.paths import data_dir

    N = max(MIN_WINDOW_ROWS, 50)
    db = SessionLocal()
    inserted_ids = []
    try:
        # Sample from training data
        df = pd.read_csv(os.path.join(data_dir(), "projects.csv"))
        sample = df.sample(n=N, random_state=0)

        # Insert prediction log rows with social_impact on API scale (0-10)
        for _, row in sample.iterrows():
            log = PredictionLog(
                budget=float(row["budget"]),
                co2_reduction=float(row["co2_reduction"]),
                social_impact=float(row["social_impact"]) / 10.0,  # API scale
                duration_months=int(row["duration_months"]),
                endpoint="evaluate",
                probability=0.75,
                latency_ms=100.0,
            )
            db.add(log)
            db.flush()
            inserted_ids.append(log.id)
        db.commit()

        # Check drift
        result = compute_drift(window=N, db=db)

        # On the base branch this FAILS: social_impact is flagged as drift
        # because we're comparing 0-10 values with 0-100 training data.
        # After the fix: no drift detected.
        assert result.status == "ok", f"Expected status='ok', got {result.status}"
        assert "social_impact" in result.features
        assert not result.features["social_impact"].drift, (
            f"social_impact should not be flagged as drift for typical API "
            f"values, but got drift={result.features['social_impact'].drift}, "
            f"p_value={result.features['social_impact'].p_value}"
        )
    finally:
        for row_id in inserted_ids:
            db.query(PredictionLog).filter(PredictionLog.id == row_id).delete()
        db.commit()
        db.close()


def test_compute_drift_control_extreme_values():
    """Control: extreme values on the model scale ARE flagged as drift.

    Insert N rows with social_impact = 1.0 (API scale), which is 10.0 on the
    model scale, far below the training median of 64. This SHOULD be flagged as
    drift.

    This test must pass BOTH before and after the fix: it verifies that the
    drift detection still works when there is actual drift.
    """
    from app.database import SessionLocal, PredictionLog
    from app.api.drift import compute_drift, MIN_WINDOW_ROWS
    from app.paths import data_dir

    N = max(MIN_WINDOW_ROWS, 50)
    db = SessionLocal()
    inserted_ids = []
    try:
        # Sample from training data for budget, co2, duration
        df = pd.read_csv(os.path.join(data_dir(), "projects.csv"))
        sample = df.sample(n=N, random_state=0)

        # Insert prediction log rows with extreme social_impact
        for _, row in sample.iterrows():
            log = PredictionLog(
                budget=float(row["budget"]),
                co2_reduction=float(row["co2_reduction"]),
                social_impact=1.0,  # API scale, far below typical
                duration_months=int(row["duration_months"]),
                endpoint="evaluate",
                probability=0.75,
                latency_ms=100.0,
            )
            db.add(log)
            db.flush()
            inserted_ids.append(log.id)
        db.commit()

        # Check drift
        result = compute_drift(window=N, db=db)

        # This MUST be flagged as drift, both before and after the fix
        assert result.status == "ok"
        assert "social_impact" in result.features
        assert result.features["social_impact"].drift, (
            f"social_impact with extreme values (all 1.0 API scale) should be "
            f"flagged as drift, but got drift={result.features['social_impact'].drift}"
        )
    finally:
        for row_id in inserted_ids:
            db.query(PredictionLog).filter(PredictionLog.id == row_id).delete()
        db.commit()
        db.close()


def test_drift_detector_baseline_check():
    """DriftDetector does not flag social_impact drift for typical values.

    Fit the baseline from data/projects.csv, add observations via /evaluate
    with typical social_impact values (6-7 on API scale), and assert that
    social_impact is not flagged as drift.

    On the base branch: FAILS because observations are on 0-10 scale while
    baseline is on 0-100 scale.
    """
    from fastapi.testclient import TestClient
    from app.main import app
    from app.drift_detection import drift_detector
    from app.paths import data_dir

    # Save current state
    original_baseline = drift_detector.get_baseline()
    original_n = drift_detector.get_baseline_n()
    original_obs = drift_detector.get_observations()

    try:
        # Fit baseline from projects.csv (the way the route does it)
        df = pd.read_csv(os.path.join(data_dir(), "projects.csv"))
        baseline = {}
        for col in ["budget", "co2_reduction", "social_impact", "duration_months"]:
            if col in df.columns:
                v = df[col].dropna()
                if len(v) > 0:
                    baseline[f"{col}_mean"] = float(v.mean())
                    baseline[f"{col}_std"] = float(v.std() or 1e-9)
        drift_detector.set_baseline(baseline)
        drift_detector.set_baseline_n(len(df))

        # Clear observations
        drift_detector._r.delete(drift_detector._k_obs)

        # Add observations via /evaluate with typical social_impact
        client = TestClient(app)
        n_obs = max(drift_detector.min_samples, 15)
        for i in range(n_obs):
            response = client.post(
                "/api/v1/evaluate",
                json={
                    "name": f"test-project-{i}",
                    "budget": 150000,
                    "co2_reduction": 800,
                    "social_impact": 6.5 if i % 2 == 0 else 7.0,  # Typical API values
                    "duration_months": 18,
                },
            )
            assert response.status_code == 200, f"Evaluate failed: {response.text}"

        # Check drift
        result = drift_detector._baseline_drift_check()

        # On the base branch: social_impact IS flagged because 6.5-7 is
        # compared with a baseline mean of ~61.
        # After the fix: no drift.
        assert result["status"] in ["stable", "drift_detected"]
        if "social_impact" in result.get("features", {}):
            feat = result["features"]["social_impact"]
            assert not feat.get("drift", False), (
                f"social_impact should not drift for typical values (6.5-7), "
                f"but got drift={feat.get('drift')}, z_score={feat.get('z_score')}"
            )
    finally:
        # Restore original state
        drift_detector.set_baseline(original_baseline)
        drift_detector.set_baseline_n(original_n)
        drift_detector._r.delete(drift_detector._k_obs)
        for obs in original_obs:
            drift_detector._r.rpush(drift_detector._k_obs, json.dumps(obs))


def test_evidently_detector_report_features():
    """Evidently detector does not flag social_impact drift for typical values.

    Build a temporary ref_stats.json from projects.csv and a request log JSONL
    with social_impact values from projects.csv / 10 (API scale). Assert that
    social_impact's status is not a drift status.

    On the base branch: FAILS because the log holds API-scale values while
    ref_stats holds model-scale distributions.
    """
    from app.drift.detector import report_features
    from app.drift.metrics import status_from_psi
    from app.paths import data_dir

    with tempfile.TemporaryDirectory() as tmpdir:
        # Build ref_stats.json from projects.csv
        df = pd.read_csv(os.path.join(data_dir(), "projects.csv"))
        ref_stats = {"n_samples": len(df), "numeric": {}, "categorical": {}}

        for col in ["budget", "co2_reduction", "social_impact", "duration_months"]:
            if col in df.columns:
                vals = df[col].dropna().values
                edges = np.percentile(vals, np.linspace(0, 100, 11))
                edges = np.unique(edges)
                counts, _ = np.histogram(vals, bins=edges)
                ref_stats["numeric"][col] = {
                    "bin_edges": edges.tolist(),
                    "counts_ref": counts.tolist(),
                    "sample_ref": vals.tolist(),
                }

        ref_path = Path(tmpdir) / "ref_stats.json"
        ref_path.write_text(json.dumps(ref_stats))

        # Create request log with API-scale social_impact
        log_dir = Path(tmpdir) / "log"
        log_dir.mkdir()
        log_path = log_dir / "2026-09-26.jsonl"

        sample = df.sample(n=50, random_state=0)
        with log_path.open("w") as f:
            for _, row in sample.iterrows():
                record = {
                    "v": 1,
                    "ts": "2026-09-26T12:00:00.000Z",
                    "model": {"alias": "champion", "version": "1"},
                    "features": {
                        "budget": float(row["budget"]),
                        "co2_reduction": float(row["co2_reduction"]),
                        "social_impact": float(row["social_impact"]) / 10.0,  # API scale
                        "duration_months": int(row["duration_months"]),
                    },
                    "pred": {"prob": 0.75, "label": 1},
                    "latency_ms": 100.0,
                }
                f.write(json.dumps(record) + "\n")

        # Monkeypatch paths
        import app.drift.detector as detector_mod
        original_ref = detector_mod.REF_PATH
        original_log = detector_mod.LOG_DIR

        try:
            detector_mod.REF_PATH = ref_path
            detector_mod.LOG_DIR = log_dir

            result = report_features(window_hours=24)

            # Find social_impact feature
            si_feat = None
            for feat in result.get("features", []):
                if feat.get("feature") == "social_impact":
                    si_feat = feat
                    break

            assert si_feat is not None, "social_impact not in features"

            # On the base branch: status is "drift" because we're comparing
            # API-scale values with model-scale reference.
            # After the fix: status should be "stable" or "warning", not "drift".
            status = si_feat.get("status")
            assert status != "drift", (
                f"social_impact should not be flagged as drift for typical "
                f"API values, but got status={status}, psi={si_feat.get('psi')}"
            )
            assert status != "not_measured", "social_impact should have been measured"
        finally:
            detector_mod.REF_PATH = original_ref
            detector_mod.LOG_DIR = original_log
