"""Managing webhooks. Owner, or a token with the admin scope (SPEC: webhooks)."""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from tahgul import audit, crypto
from tahgul import webhooks as wh
from tahgul.crud import apply_changes, require_admin
from tahgul.db import get_db
from tahgul.deps import require_scope
from tahgul.models import Webhook, WebhookDelivery
from tahgul.principal import Principal
from tahgul.timeutil import utcnow

router = APIRouter(prefix="/api/v1", tags=["webhooks"])


def admin(principal: Principal = Depends(require_scope("read"))) -> Principal:
    require_admin(principal, "webhooks")
    return principal


def _check_url(v: str) -> str:
    v = v.strip()
    if not (v.startswith("http://") or v.startswith("https://")) or len(v) > 1000:
        raise ValueError("use an http:// or https:// URL")
    return v


def _check_events(v: list[str]) -> list[str]:
    v = sorted(set(v))
    unknown = [e for e in v if e != "*" and e not in wh.EVENTS]
    if unknown:
        raise ValueError(f"unknown events: {', '.join(unknown)}")
    if not v:
        raise ValueError("pick at least one event (or * for all)")
    return v


class WebhookIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(description="Where to POST, e.g. http://agent.lan:8787/tahgul")
    events: list[str] = Field(description='Event names, or ["*"]')
    active: bool = True

    _url = field_validator("url")(_check_url)
    _events = field_validator("events")(_check_events)


class WebhookPatch(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str | None = Field(None, min_length=1, max_length=100)
    url: str | None = None
    events: list[str] | None = None
    active: bool | None = None

    _url = field_validator("url")(lambda v: v if v is None else _check_url(v))
    _events = field_validator("events")(lambda v: v if v is None else _check_events(v))


class WebhookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    url: str
    events: list[str]
    active: bool
    last_delivery_at: datetime | None
    last_status: str | None
    created_at: datetime


class WebhookWithSecret(WebhookOut):
    secret: str = Field(description="Shown once. Use it to check X-Tahgul-Signature.")


class DeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    webhook_id: uuid.UUID
    event: str
    status: str
    attempts: int
    next_attempt_at: datetime
    response_code: int | None
    error: str | None
    created_at: datetime
    payload: dict[str, Any]


class EventInfo(BaseModel):
    name: str
    description: str


def _get(db: Session, hook_id: uuid.UUID) -> Webhook:
    h = db.get(Webhook, hook_id)
    if h is None or h.deleted_at is not None:
        raise HTTPException(404, detail="webhook_not_found")
    return h


@router.get("/webhooks/events", response_model=list[EventInfo])
def list_events(_: Principal = Depends(require_scope("read"))):
    return [EventInfo(name=k, description=v) for k, v in wh.EVENTS.items()]


@router.get("/webhooks", response_model=list[WebhookOut])
def list_webhooks(_: Principal = Depends(admin), db: Session = Depends(get_db)):
    return db.scalars(
        select(Webhook).where(Webhook.deleted_at.is_(None)).order_by(Webhook.created_at)
    ).all()


@router.post("/webhooks", response_model=WebhookWithSecret, status_code=201)
def create_webhook(
    body: WebhookIn, principal: Principal = Depends(admin), db: Session = Depends(get_db)
):
    secret = wh.new_secret()
    h = Webhook(**body.model_dump(), secret_encrypted=crypto.encrypt(secret))
    db.add(h)
    db.flush()
    audit.record(db, principal, "create", "webhook", h.id, {"name": h.name, "url": h.url})
    db.commit()
    return WebhookWithSecret(**WebhookOut.model_validate(h).model_dump(), secret=secret)


@router.patch("/webhooks/{hook_id}", response_model=WebhookOut)
def update_webhook(
    hook_id: uuid.UUID,
    body: WebhookPatch,
    principal: Principal = Depends(admin),
    db: Session = Depends(get_db),
):
    h = _get(db, hook_id)
    changes = apply_changes(h, body.model_dump(exclude_unset=True, exclude_none=True))
    if changes:
        audit.record(db, principal, "update", "webhook", h.id, changes)
        db.commit()
    return h


@router.post("/webhooks/{hook_id}/rotate-secret", response_model=WebhookWithSecret)
def rotate_secret(
    hook_id: uuid.UUID, principal: Principal = Depends(admin), db: Session = Depends(get_db)
):
    h = _get(db, hook_id)
    secret = wh.new_secret()
    h.secret_encrypted = crypto.encrypt(secret)
    audit.record(db, principal, "rotate_secret", "webhook", h.id, {})
    db.commit()
    return WebhookWithSecret(**WebhookOut.model_validate(h).model_dump(), secret=secret)


@router.delete("/webhooks/{hook_id}", status_code=204)
def delete_webhook(
    hook_id: uuid.UUID, principal: Principal = Depends(admin), db: Session = Depends(get_db)
):
    h = _get(db, hook_id)
    h.deleted_at, h.active = utcnow(), False
    audit.record(db, principal, "delete", "webhook", h.id, {"name": h.name})
    db.commit()


@router.post("/webhooks/{hook_id}/test", response_model=DeliveryOut)
def test_webhook(
    hook_id: uuid.UUID, principal: Principal = Depends(admin), db: Session = Depends(get_db)
):
    """Send a `ping` now and report what the receiver said."""
    h = _get(db, hook_id)
    d_id = uuid.uuid4()
    d = WebhookDelivery(
        id=d_id,
        webhook_id=h.id,
        event="ping",
        payload=wh._payload("ping", {"webhook": h.name, "by": principal.name}, d_id),
        next_attempt_at=utcnow(),
        status="pending",
    )
    db.add(d)
    db.flush()
    code, err = wh.send(h, d)
    d.attempts, d.response_code, d.error = 1, code, err
    d.status = "sent" if err is None else "failed"
    h.last_delivery_at, h.last_status = utcnow(), "ok" if err is None else "failed"
    db.commit()
    return d


@router.get("/webhooks/{hook_id}/deliveries", response_model=list[DeliveryOut])
def list_deliveries(
    hook_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    _: Principal = Depends(admin),
    db: Session = Depends(get_db),
):
    _get(db, hook_id)
    return db.scalars(
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == hook_id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(limit)
    ).all()


@router.post("/webhook-deliveries/{delivery_id}/retry", response_model=DeliveryOut)
def retry_delivery(
    delivery_id: uuid.UUID, principal: Principal = Depends(admin), db: Session = Depends(get_db)
):
    """Send a delivery again (same id and payload, so receivers can de-duplicate)."""
    d = db.get(WebhookDelivery, delivery_id)
    if d is None:
        raise HTTPException(404, detail="delivery_not_found")
    d.status, d.next_attempt_at, d.error = "pending", utcnow(), None
    db.commit()
    wh.deliver_pending()
    db.refresh(d)
    return d
