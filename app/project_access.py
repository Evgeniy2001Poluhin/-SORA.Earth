"""Who may read which project rows, as SQL.

Two predicates, and they answer different questions on purpose.

`project_read_predicate(model, actor)` is for a person looking at rows: the
history, one evaluation by id, the CSV, the predictions log.

    specialist        their own pilot rows, nothing else
    analyst, viewer   the shared corpus only (data_scope = 'public')
    admin             every row -- and the caller must record the read in the
                      durable audit (app/project_audit.py) before returning it

`shared_data_predicate(model)` is for everything that is not a person: trends,
data health, drift, forecasting, training, diagnostics. It takes no actor,
because who is asking must not widen what a shared figure is computed from --
an administrator's Bearer on /trends still gets the public series.

Both are applied in the query, before COUNT, OFFSET and LIMIT. Filtering after
the fetch would page through rows the caller cannot see and report a total
that counts them.

The shared filter is a positive match on `public`. `!= 'pilot'` or
`owner_user_id IS NULL` would admit any scope added later; a positive match
excludes it until somebody decides it belongs in the shared corpus.
"""
import uuid

import sqlalchemy as sa
from fastapi import Depends, HTTPException

from app.auth import UserInfo, optional_auth

PUBLIC = "public"
PILOT = "pilot"

#: For raw SQL readers, which cannot take a column expression. Kept beside the
#: ORM predicate so the two cannot be changed apart.
PUBLIC_SCOPE_SQL = "data_scope = 'public'"

#: Roles that can save an evaluation of their own. Analyst and viewer work in
#: the shared corpus and have nothing private to save into.
SAVING_ROLES = frozenset({"specialist", "admin"})

#: Keys a request body may not carry. The owner is the caller's account and the
#: scope follows from it; neither is something a client chooses. Checked on the
#: raw JSON, because the route reads that separately from the validated model
#: and an ignored extra field would otherwise travel on unnoticed.
FORBIDDEN_BODY_KEYS = frozenset({"owner_user_id", "user_id", "data_scope"})


def shared_data_predicate(model):
    """The shared corpus. Every consumer that is not a person reading rows."""
    return model.data_scope == PUBLIC


def project_read_predicate(model, actor: UserInfo):
    """The rows `actor` may read. Admin sees everything and must be audited."""
    if actor.role == "specialist":
        return sa.and_(model.data_scope == PILOT,
                       model.owner_user_id == uuid.UUID(actor.user_id))
    if actor.role in ("analyst", "viewer"):
        return model.data_scope == PUBLIC
    if actor.role == "admin":
        return sa.true()
    # A role this module has never heard of reads nothing, rather than falling
    # through to whichever branch happens to be last.
    return sa.false()


def is_service_read(actor: UserInfo) -> bool:
    """True when the read must go through the durable audit."""
    return actor.role == "admin"


def refuse_specialist(user=Depends(optional_auth)):
    """403 for a specialist on a surface outside the ownership model.

    Copilot sessions, batch results and the prediction CSV have their own
    storage with no owner column. Until they have one, a specialist is kept off
    them on the server -- hiding a menu entry is not a control. Anonymous and
    the other roles are unaffected: this adds a refusal, it does not add
    authentication where a route had none.
    """
    if user is not None and user.role == "specialist":
        raise HTTPException(status_code=403,
                            detail="Not available to the specialist role")
    return user
