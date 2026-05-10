"""Smoke tests for PSI + KS drift metrics."""
import numpy as np
from app.drift.metrics import psi, ks, status_from_psi


def test_psi_stable_distributions():
    rng = np.random.default_rng(0)
    ref = np.histogram(rng.normal(0, 1, 10000), bins=10)[0]
    live = np.histogram(rng.normal(0, 1, 10000), bins=10)[0]
    assert psi(ref, live) < 0.05


def test_psi_drift_detected():
    rng = np.random.default_rng(0)
    edges = np.linspace(-4, 4, 11)
    ref = np.histogram(rng.normal(0, 1, 10000), bins=edges)[0]
    live = np.histogram(rng.normal(1.5, 1, 10000), bins=edges)[0]
    assert psi(ref, live) > 0.25
    assert status_from_psi(psi(ref, live)) == "drift"


def test_ks_detects_shift():
    rng = np.random.default_rng(0)
    stat, p = ks(rng.normal(0, 1, 500), rng.normal(0.8, 1, 500))
    assert stat > 0.2 and p < 0.01
