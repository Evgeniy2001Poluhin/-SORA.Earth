"""Reading the durable record of administrator access to project rows.

Read-only. There is no route to edit or delete an event: a record the people
it describes can rewrite is not a record. Protection from someone with direct
database access is out of scope here.

This is not /audit/log. That route serves app/audit.py, an in-memory deque of
logins and account changes in whichever worker answers; this one reads
`project_access_events`, which every worker writes and nothing forgets.
"""
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.exc import SQLAlchemyError

from app.auth import UserInfo, require_admin
from app import database
from app.database import ProjectAccessEvent, ProjectAccessTarget
from app.schemas import ProjectAccessEventView, ProjectAccessLogPage, ProjectAccessTargetView

router = APIRouter(prefix="/admin", tags=["admin"])


_EVENT_FIELDS = tuple(f for f in ProjectAccessEventView.model_fields if f != "targets")
_UUID_FIELDS = ("id", "actor_user_id", "request_id")


def _event_view(event, targets) -> ProjectAccessEventView:
    # Copied field by field from the view's own declaration, so a column added
    # to the table reaches this response only by being added to the view.
    fields = {f: getattr(event, f) for f in _EVENT_FIELDS}
    for f in _UUID_FIELDS:
        fields[f] = str(fields[f])
    return ProjectAccessEventView(targets=targets, **fields)


@router.get("/project-access-log", response_model=ProjectAccessLogPage)
def project_access_log(
    response: Response,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    actor_user_id: Optional[uuid.UUID] = Query(None),
    action: Optional[Literal["list", "detail", "export", "delete", "clear", "log_read"]] = Query(None),
    resource_id: Optional[int] = Query(None, description="Only events that touched this evaluation "
                                                         "or log row id."),
    user: UserInfo = Depends(require_admin),
):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        with database.SessionLocal() as session:
            q = session.query(ProjectAccessEvent)
            if actor_user_id is not None:
                q = q.filter(ProjectAccessEvent.actor_user_id == actor_user_id)
            if action is not None:
                q = q.filter(ProjectAccessEvent.action == action)
            if resource_id is not None:
                q = q.filter(ProjectAccessEvent.id.in_(
                    session.query(ProjectAccessTarget.event_id)
                    .filter(ProjectAccessTarget.resource_id == resource_id)))
            total = q.count()
            events = (q.order_by(ProjectAccessEvent.occurred_at.desc(), ProjectAccessEvent.id)
                      .offset(offset).limit(limit).all())
            by_event = {e.id: [] for e in events}
            if by_event:
                for t in (session.query(ProjectAccessTarget)
                          .filter(ProjectAccessTarget.event_id.in_(list(by_event)))
                          .order_by(ProjectAccessTarget.id)):
                    by_event[t.event_id].append(ProjectAccessTargetView(
                        resource_type=t.resource_type, resource_id=t.resource_id,
                        owner_user_id=str(t.owner_user_id) if t.owner_user_id else None,
                        data_scope=t.data_scope, effect=t.effect))
            items = [_event_view(e, by_event[e.id]) for e in events]
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="The access log could not be read",
                            headers={"Retry-After": "5"}) from None
    return ProjectAccessLogPage(items=items, total=total, limit=limit, offset=offset)
