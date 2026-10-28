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
