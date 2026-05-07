import pytest
from app.calibration_metrics import (
    brier_score, reliability_curve, expected_calibration_error, murphy_decomposition,
)


def test_brier_perfect():
    assert brier_score([0.0, 0.0, 1.0, 1.0], [0, 0, 1, 1]) == 0.0


def test_brier_worst():
    assert brier_score([1.0, 1.0, 0.0, 0.0], [0, 0, 1, 1]) == 1.0


def test_brier_random():
    assert brier_score([0.5, 0.5, 0.5, 0.5], [0, 1, 0, 1]) == 0.25


def test_ece_two_bins():
    ece = expected_calibration_error([0.1, 0.1, 0.9, 0.9], [0, 0, 1, 1], n_bins=2)
    assert abs(ece - 0.1) < 1e-6


def test_reliability_shape():
    c = reliability_curve([0.05, 0.15, 0.25, 0.95], [0, 0, 0, 1], n_bins=10)
    assert len(c["bin_lower"]) == 10
    assert sum(c["count"]) == 4
    assert c["count"][9] == 1
    assert c["mean_observed"][9] == 1.0


def test_reliability_empty_bins_none():
    c = reliability_curve([0.05, 0.95], [0, 1], n_bins=10)
    for i in range(1, 9):
        assert c["count"][i] == 0
        assert c["mean_predicted"][i] is None


def test_murphy_identity():
    probs = [0.1, 0.3, 0.7, 0.9, 0.4, 0.6]
    labels = [0, 0, 1, 1, 0, 1]
    d = murphy_decomposition(probs, labels, n_bins=10)
    rhs = d["reliability"] - d["resolution"] + d["uncertainty"]
    assert abs(d["brier"] - rhs) < 1e-3


def test_validate_length_mismatch():
    with pytest.raises(ValueError, match="length mismatch"):
        brier_score([0.5], [0, 1])


def test_validate_proba_out_of_range():
    with pytest.raises(ValueError, match="out of"):
        brier_score([1.5], [1])


def test_validate_label_non_binary():
    with pytest.raises(ValueError, match="binary"):
        brier_score([0.5], [2])


def test_empty_input():
    with pytest.raises(ValueError, match="empty"):
        brier_score([], [])
