"""The durable record of an administrator reading or removing project rows.

An administrator can read every specialist's saved evaluations. That access is
needed -- support, an investigation, a deletion request -- and it is only
acceptable if it leaves a trace nobody can quietly skip. app/audit.py cannot be
that trace: it is a deque in one worker's memory, gone on restart.

The rule for a read is that the record is committed before the data leaves:

    check the role -> read the permitted rows -> build the response
    -> write the event and one target per row -> commit -> return

`record()` adds the rows to the caller's session; `commit_or_refuse()` commits
and turns any failure into a 503. If the audit cannot be written, the data is
not returned. There is deliberately no `except: log a warning; return data`,
no background task and no middleware writing after the response: each of those
lets the read happen while the record of it is lost.

For a CSV the event means "the export was prepared and released", not that the
client received every byte. For a deletion the event and the DELETE are added
to the same session, so they commit or roll back together.

Every administrator request is recorded, including one that finds nothing and
one that reads the administrator's own projects. An exception for "only their
own" is a branch somebody has to get right every time; recording everything is
simpler and cannot be wrong in that direction.
"""
import logging
import uuid

from fastapi import HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from app.auth import UserInfo
from app.database import ProjectAccessEvent, ProjectAccessTarget

logger = logging.getLogger(__name__)

EVALUATION = "evaluation"
PREDICTION_LOG = "prediction_log"


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path:
        # The template, not the concrete path: an id in a URL is already a
        # target row below, and the template is what groups requests.
        root = request.scope.get("root_path", "") or ""
        return (root + path)[:255]
    return request.url.path[:255]


def target(resource_type, row=None, *, resource_id=None, effect):
    """One target. `row` gives the id and the owner/scope snapshot; a
    not_found target names only the id that was asked for."""
    if row is None:
        return dict(resource_type=resource_type, resource_id=int(resource_id),
                    owner_user_id=None, data_scope=None, effect=effect)
    return dict(resource_type=resource_type, resource_id=int(row.id),
                owner_user_id=row.owner_user_id, data_scope=row.data_scope, effect=effect)


def record(session, *, actor: UserInfo, request: Request, action: str, status_code: int,
           outcome: str = "ok", targets=(), filters=None, returned_count=None,
           matched_count=None, affected_count=None, reason=None) -> uuid.UUID:
    """Add one event and its targets to `session`. Does not commit."""
    event_id = uuid.uuid4()
    session.add(ProjectAccessEvent(
        id=event_id,
        actor_user_id=uuid.UUID(actor.user_id),
        actor_username=actor.username,
        actor_role=actor.role,
        action=action,
        method=request.method[:8],
        route_template=_route_template(request),
        status_code=status_code,
        outcome=outcome,
        filters=filters or None,
        returned_count=returned_count,
        matched_count=matched_count,
        affected_count=affected_count,
        reason=reason,
    ))
    # No flush here: the unit of work inserts the event before its targets by
    # their foreign key, and every database error then surfaces in one place,
    # commit_or_refuse.
    for t in targets:
        session.add(ProjectAccessTarget(event_id=event_id, **t))
    return event_id


def commit_or_refuse(session):
    """Commit the audit -- and whatever else the session holds -- or 503.

    Rolled back on failure, so a deletion that could not be recorded did not
    happen either.
    """
    try:
        session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        logger.error("project access audit could not be committed: %s", type(exc).__name__)
        raise HTTPException(status_code=503,
                            detail="The access could not be recorded, so it was not performed",
                            headers={"Retry-After": "5"}) from None


class _Deleted:
    """A row as DELETE ... RETURNING reported it: what `target()` reads."""

    def __init__(self, row):
        self.id, self.owner_user_id, self.data_scope = row


def _returning(session, *where):
    from sqlalchemy import delete

    from app.database import Evaluation
    result = session.execute(
        delete(Evaluation).where(*where)
        .returning(Evaluation.id, Evaluation.owner_user_id, Evaluation.data_scope)
        .execution_options(synchronize_session=False))
    return [_Deleted(r) for r in result.all()]


def delete_evaluation(session, *, actor: UserInfo, request: Request, eval_id: int):
    """Delete one evaluation and record exactly what was deleted. Not committed.

    The record is built from the DELETE's own RETURNING, not from a read before
    it. Two administrators deleting the same id at once both used to read the
    row, both record it as `deleted` with affected_count 1, and only one of
    them had deleted anything. On PostgreSQL the second DELETE waits on the
    first's row lock and then finds nothing, so it records a not_found with
    nothing affected -- which is what happened.

    Returns the deleted row, or None. The caller commits with
    commit_or_refuse, so the deletion and its record land together.
    """
    from app.database import Evaluation
    deleted = _returning(session, Evaluation.id == eval_id)
    if not deleted:
        record(session, actor=actor, request=request, action="delete", status_code=404,
               outcome="not_found", affected_count=0,
               targets=[target(EVALUATION, resource_id=eval_id, effect="not_found")])
        return None
    record(session, actor=actor, request=request, action="delete", status_code=200,
           affected_count=len(deleted),
           targets=[target(EVALUATION, row, effect="deleted") for row in deleted])
    return deleted[0]


def clear_public(session, *, actor: UserInfo, request: Request) -> int:
    """Delete every public evaluation and record the ids actually deleted.

    Same rule as delete_evaluation: a concurrent clear that found the rows
    already gone records zero, not the list it would have deleted.
    """
    from app.database import Evaluation
    from app.project_access import PUBLIC, shared_data_predicate
    deleted = _returning(session, shared_data_predicate(Evaluation))
    record(session, actor=actor, request=request, action="clear", status_code=200,
           filters={"data_scope": PUBLIC}, affected_count=len(deleted),
           targets=[target(EVALUATION, row, effect="deleted") for row in deleted])
    return len(deleted)
