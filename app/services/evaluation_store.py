"""Saving an evaluation: the `evaluations` row and its `predictions_log` row,
together or not at all.

This is the only writer of `predictions_log`. It used to be
`app.main.log_prediction`, which opened its own session, committed on its own
and swallowed every exception, while `/evaluate` committed the `Evaluation` in a
second session afterwards. A failure between the two left a log row with no
evaluation, and a failure inside the first was a line in a log file while the
response went out as though nothing had happened.

Now both rows are added to one session, flushed, and committed once. The
caller is told `saved` only after that commit returns; an exception propagates
and the route answers 503. Retrying a request that timed out after the commit
saves a second evaluation -- idempotency is not part of this.

Both rows carry the same owner and `data_scope = 'pilot'`. The owner is the
signed-in caller and nothing else: this module takes it as an argument from the
route, which takes it from the verified token, never from the request body.
"""
import uuid
from datetime import datetime

from app import database
from app.database import Evaluation, PredictionLog
from app.project_access import PILOT


def prediction_log_row(*, endpoint, project, result, latency_ms, model_version,
                       threshold, owner_user_id, data_scope):
    """The `predictions_log` row for one evaluation. Builds; does not write.

    `probability` is the champion's figure, as a percentage: `calculate_esg`
    returns it as `success_probability` and has no `probability` key, so the
    fallback is the value that is actually used. `prediction` applies the
    serving threshold, which is a fraction.
    """
    probability = result.get("probability") or result.get("success_probability")
    prediction = None
    if probability is not None:
        prediction = 1 if (probability / 100.0) >= threshold else 0
    return PredictionLog(
        endpoint=endpoint,
        budget=project.budget,
        co2_reduction=project.co2_reduction,
        social_impact=project.social_impact,
        duration_months=project.duration_months,
        category=getattr(project, "category", None),
        region=getattr(project, "region", None),
        prediction=prediction,
        probability=probability,
        esg_total_score=result.get("total_score"),
        latency_ms=latency_ms,
        model_version=model_version,
        owner_user_id=owner_user_id,
        data_scope=data_scope,
    )


def evaluation_row(*, project, result, recommendation, region_name, lat, lon,
                   owner_user_id, data_scope):
    return Evaluation(
        name=project.name,
        budget=project.budget,
        co2_reduction=project.co2_reduction,
        social_impact=project.social_impact,
        duration_months=project.duration_months,
        total_score=result["total_score"],
        environment_score=result["environment_score"],
        social_score=result["social_score"],
        economic_score=result["economic_score"],
        success_probability=result["success_probability"],
        recommendation=recommendation,
        risk_level=result["risk_level"],
        created_at=datetime.now(),
        region=region_name,
        lat=lat,
        lon=lon,
        owner_user_id=owner_user_id,
        data_scope=data_scope,
    )


def save_evaluation(*, owner_user_id: uuid.UUID, project, result, recommendation,
                    region_name, lat, lon, latency_ms, model_version, threshold,
                    session_factory=None) -> int:
    """Write both rows in one transaction and return the evaluation's id.

    Raises whatever the database raised; nothing is caught here. A partial
    save is impossible by construction, and a failed one must not be reported
    as saved.
    """
    if owner_user_id is None:
        # The CHECK would refuse it too; this says why before the database has
        # to.
        raise ValueError("a saved evaluation needs an owner")
    # Looked up per call rather than bound at import, so the session factory
    # in force is the one the application is using now.
    factory = session_factory or database.SessionLocal
    with factory() as session:
        try:
            evaluation = evaluation_row(
                project=project, result=result, recommendation=recommendation,
                region_name=region_name, lat=lat, lon=lon,
                owner_user_id=owner_user_id, data_scope=PILOT)
            log_row = prediction_log_row(
                endpoint="evaluate", project=project, result=result,
                latency_ms=latency_ms, model_version=model_version,
                threshold=threshold, owner_user_id=owner_user_id, data_scope=PILOT)
            session.add(evaluation)
            session.add(log_row)
            session.flush()
            evaluation_id = int(evaluation.id)
            session.commit()
        except BaseException:
            session.rollback()
            raise
    return evaluation_id
