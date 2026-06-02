"""Webhook subscription management."""
import secrets
from fastapi import APIRouter, Depends, HTTPException
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

@router.post("")
def create_sub(body: SubIn, db: Session = Depends(get_db)):
    sub = WebhookSubscription(url=str(body.url), event_type=body.event_type, secret=secrets.token_hex(16))
    db.add(sub); db.commit(); db.refresh(sub)
    return {"id": sub.id, "url": sub.url, "event_type": sub.event_type, "secret": sub.secret, "active": sub.active}

@router.get("")
def list_subs(db: Session = Depends(get_db)):
    return [{"id": s.id, "url": s.url, "event_type": s.event_type, "active": s.active,
             "created_at": s.created_at.isoformat() if s.created_at else None}
            for s in db.query(WebhookSubscription).all()]

@router.get("/deliveries")
def list_deliveries(db: Session = Depends(get_db)):
    rows = db.query(WebhookDelivery).order_by(WebhookDelivery.id.desc()).limit(50).all()
    return [{"id": d.id, "subscription_id": d.subscription_id, "event_type": d.event_type,
             "status_code": d.status_code, "ok": d.ok, "error": d.error,
             "created_at": d.created_at.isoformat() if d.created_at else None} for d in rows]

@router.delete("/{sub_id}")
def delete_sub(sub_id: int, db: Session = Depends(get_db)):
    s = db.get(WebhookSubscription, sub_id)
    if not s: raise HTTPException(404, "not found")
    db.delete(s); db.commit()
    return {"deleted": sub_id}
