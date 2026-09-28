"""predictions_log.prediction is written with the threshold-based label.

Finding 19: `predictions_log.prediction` was always NULL because `calculate_esg`
does not return a "prediction" key, and `log_prediction` tried to get one.

The fix computes the prediction from the probability and the model's
best_threshold: prediction = 1 if probability >= threshold*100 else 0.
"""
import pytest


def test_evaluate_writes_prediction_based_on_threshold(client):
    """The /evaluate endpoint writes predictions_log.prediction as 0 or 1."""
    from app.database import PredictionLog
    from app.main import get_db_sync, best_threshold

    # High-probability project (should predict 1)
    high_prob_project = {
        "name": "High Success Project",
        "budget": 200000,
        "co2_reduction": 500,
        "social_impact": 9,
        "duration_months": 24,
        "category": "Solar Energy",
        "region": "Europe"
    }

    db = get_db_sync()
    try:
        before_count = db.query(PredictionLog).count()
    finally:
        db.close()

    r = client.post("/api/v1/evaluate", json=high_prob_project)
    assert r.status_code == 200, r.text
    result = r.json()

    # Get the logged prediction
    db = get_db_sync()
    try:
        log_entry = db.query(PredictionLog).filter(
            PredictionLog.endpoint == "evaluate"
        ).order_by(PredictionLog.id.desc()).first()

        assert log_entry is not None, "No log entry found"
        assert log_entry.probability is not None, "probability should be written"
        assert log_entry.prediction is not None, "prediction should be written (Finding 19 fix)"

        # Verify the prediction is correct based on threshold
        expected_prediction = 1 if log_entry.probability >= best_threshold * 100 else 0
        assert log_entry.prediction == expected_prediction, (
            f"prediction={log_entry.prediction} but expected {expected_prediction} "
            f"(probability={log_entry.probability}, threshold={best_threshold*100})"
        )

        # High probability should predict positive
        assert log_entry.probability >= best_threshold * 100, (
            f"Test project should have high probability, got {log_entry.probability}"
        )
        assert log_entry.prediction == 1, "High probability should predict 1"

        after_count = db.query(PredictionLog).count()
        assert after_count == before_count + 1, "Exactly one row should be written"
    finally:
        db.close()


def test_low_probability_predicts_zero(client):
    """A project with probability below threshold gets prediction=0."""
    from app.database import PredictionLog
    from app.main import get_db_sync, best_threshold

    # Low-probability project
    low_prob_project = {
        "name": "Low Success Project",
        "budget": 10000,
        "co2_reduction": 10,
        "social_impact": 2,
        "duration_months": 6,
        "category": "Solar Energy",
        "region": "Europe"
    }

    r = client.post("/api/v1/evaluate", json=low_prob_project)
    assert r.status_code == 200, r.text

    db = get_db_sync()
    try:
        log_entry = db.query(PredictionLog).filter(
            PredictionLog.endpoint == "evaluate"
        ).order_by(PredictionLog.id.desc()).first()

        assert log_entry is not None
        assert log_entry.probability is not None
        assert log_entry.prediction is not None

        # Verify the threshold logic
        expected_prediction = 1 if log_entry.probability >= best_threshold * 100 else 0
        assert log_entry.prediction == expected_prediction

        # Low probability should predict negative
        assert log_entry.probability < best_threshold * 100, (
            f"Test project should have low probability, got {log_entry.probability}"
        )
        assert log_entry.prediction == 0, "Low probability should predict 0"
    finally:
        db.close()


def test_prediction_matches_probability_across_range(client):
    """The prediction column correctly reflects the probability for various inputs."""
    from app.database import PredictionLog
    from app.main import get_db_sync, best_threshold

    # Different budget levels to get different probabilities
    test_cases = [
        {"budget": 10000, "co2_reduction": 50, "social_impact": 3},
        {"budget": 50000, "co2_reduction": 100, "social_impact": 5},
        {"budget": 100000, "co2_reduction": 200, "social_impact": 7},
        {"budget": 200000, "co2_reduction": 400, "social_impact": 9},
    ]

    db = get_db_sync()
    try:
        before_count = db.query(PredictionLog).count()
    finally:
        db.close()

    for i, case in enumerate(test_cases):
        project = {
            "name": f"Test Project {i}",
            "duration_months": 18,
            "category": "Solar Energy",
            "region": "Europe",
            **case
        }
        r = client.post("/api/v1/evaluate", json=project)
        assert r.status_code == 200, f"Case {i} failed: {r.text}"

    # Verify all predictions
    db = get_db_sync()
    try:
        logs = db.query(PredictionLog).filter(
            PredictionLog.endpoint == "evaluate"
        ).order_by(PredictionLog.id.desc()).limit(len(test_cases)).all()

        assert len(logs) == len(test_cases), f"Expected {len(test_cases)} new logs"

        for log in logs:
            assert log.prediction is not None, "prediction must be written"
            assert log.probability is not None, "probability must be written"

            # Verify threshold logic
            expected = 1 if log.probability >= best_threshold * 100 else 0
            assert log.prediction == expected, (
                f"prediction={log.prediction} but expected {expected} "
                f"for probability={log.probability}"
            )

        after_count = db.query(PredictionLog).count()
        assert after_count == before_count + len(test_cases)
    finally:
        db.close()


def test_prediction_is_integer_zero_or_one(client):
    """The prediction column stores integer 0 or 1, not None."""
    from app.database import PredictionLog
    from app.main import get_db_sync

    project = {
        "name": "Test",
        "budget": 100000,
        "co2_reduction": 150,
        "social_impact": 7,
        "duration_months": 24,
        "category": "Solar Energy",
        "region": "Europe"
    }

    r = client.post("/api/v1/evaluate", json=project)
    assert r.status_code == 200, r.text

    db = get_db_sync()
    try:
        log_entry = db.query(PredictionLog).filter(
            PredictionLog.endpoint == "evaluate"
        ).order_by(PredictionLog.id.desc()).first()

        assert log_entry.prediction in (0, 1), (
            f"prediction must be 0 or 1, got {log_entry.prediction}"
        )
        assert isinstance(log_entry.prediction, int), (
            f"prediction must be int, got {type(log_entry.prediction)}"
        )
    finally:
        db.close()
