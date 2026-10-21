"""Webhooks: outgoing HTTP calls when things happen (SPEC: agent API, "Webhooks")."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tahgul.models.base import Base, RecordMixin, UTCDateTime


class Webhook(RecordMixin, Base):
    __tablename__ = "webhooks"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    # Event names, or ["*"] for everything.
    events: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # Signing secret, encrypted with TAHGUL_SECRET_KEY.
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(nullable=False, default=True)
    last_delivery_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_status: Mapped[str | None] = mapped_column(String(10))


class WebhookDelivery(RecordMixin, Base):
    """One event for one webhook: written with the change that caused it (an outbox)."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        UniqueConstraint("webhook_id", "dedupe_key"),
        Index("ix_webhook_deliveries_due", "status", "next_attempt_at"),
    )

    webhook_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("webhooks.id"), index=True, nullable=False
    )
    event: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # pending | sent | failed
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    response_code: Mapped[int | None] = mapped_column()
    error: Mapped[str | None] = mapped_column(String(500))
    # For scheduled events (due soon, meeting ended): one delivery per thing.
    dedupe_key: Mapped[str | None] = mapped_column(String(200))

    webhook: Mapped[Webhook] = relationship()
