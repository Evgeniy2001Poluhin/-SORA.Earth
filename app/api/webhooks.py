"""Webhook subscription management."""
import secrets

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_admin
from app.services.outbound import OutboundRefused, check_outbound_url

# No validation at import. An earlier version called validate_configuration()
# here, which made every importer of this module -- alembic, the CLI scripts, the
# backup tooling, test collection -- fail on a webhook setting none of them use.
# A module-level side effect couples things that have nothing to do with each
# other. The check belongs to application startup, and lives in app/main.py.
from pydantic import BaseModel, HttpUrl
from sqlalchemy.orm import Session
from app.database import SessionLocal, WebhookSubscription, WebhookDelivery

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

class SubIn(BaseModel):
    url: HttpUrl
    event_type: str = "drift"

# Two separate controls, because they answer different questions.
#
# require_admin answers "may this caller register a webhook". Registration was
# open to anyone, which meant anyone could make this server issue an outbound
# request to an address of their choosing.
#
# check_outbound_url answers "may this server fetch that address". Authorisation
# does not make a URL safe: an administrator can equally point a webhook at the
# database, and HttpUrl validates the shape of a URL and nothing about where it
# leads.
@router.post("", dependencies=[Depends(require_admin)])
def create_sub(body: SubIn, db: Session = Depends(get_db)):
    try:
        url = check_outbound_url(str(body.url))
    except OutboundRefused as exc:
        raise HTTPException(400, f"webhook url refused: {exc}")
    sub = WebhookSubscription(url=url, event_type=body.event_type, secret=secrets.token_hex(16))
    db.add(sub); db.commit(); db.refresh(sub)
    return {"id": sub.id, "url": sub.url, "event_type": sub.event_type, "secret": sub.secret, "active": sub.active}

@router.get("")
def list_subs(db: Session = Depends(get_db)):
    return [{"id": s.id, "url": s.url, "event_type": s.event_type, "active": s.active,
             "created_at": s.created_at.isoformat() if s.created_at else None}
            for s in db.query(WebhookSubscription).all()]

# The delivery record carries the outcome of a request this server made to an
# address someone else chose. Served without authentication it is an oracle: ask
# for an address, read back whether it answered. Admin-only, and the error text
# is now a category rather than the network's own words -- see safe_error.
@router.get("/deliveries", dependencies=[Depends(require_admin)])
def list_deliveries(db: Session = Depends(get_db)):
    rows = db.query(WebhookDelivery).order_by(WebhookDelivery.id.desc()).limit(50).all()
    return [{"id": d.id, "subscription_id": d.subscription_id, "event_type": d.event_type,
             "status_code": d.status_code, "ok": d.ok, "error": d.error,
             "created_at": d.created_at.isoformat() if d.created_at else None} for d in rows]

@router.delete("/{sub_id}", dependencies=[Depends(require_admin)])
def delete_sub(sub_id: int, db: Session = Depends(get_db)):
    s = db.get(WebhookSubscription, sub_id)
    if not s: raise HTTPException(404, "not found")
    db.delete(s); db.commit()
    return {"deleted": sub_id}
