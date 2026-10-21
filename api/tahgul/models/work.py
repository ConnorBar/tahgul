"""Workspaces, clients, projects and activities (SPEC: core concepts and data model)."""

import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tahgul.models.base import Base, RecordMixin

# Money and rates are exact decimals, never floats.
Rate = Numeric(10, 2)
Money = Numeric(12, 2)
Hours = Numeric(10, 2)
Percent = Numeric(6, 3)

PROJECT_STATUSES = ("idea", "active", "paused", "done")
DEFAULT_INVOICE_PATTERN = "{Y}-{seq:3}"


class Workspace(RecordMixin, Base):
    """A top-level space, e.g. "Consulting" or "Personal"."""

    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    default_rate: Mapped[Decimal | None] = mapped_column(Rate)
    # Blank = no tax line on invoices.
    tax_rate: Mapped[Decimal | None] = mapped_column(Percent)
    payment_terms_days: Mapped[int] = mapped_column(nullable=False, default=30)
    invoice_number_pattern: Mapped[str] = mapped_column(
        String(50), nullable=False, default=DEFAULT_INVOICE_PATTERN
    )
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="America/Chicago")
    # Rounding is applied when invoicing, never to the raw entries (SPEC: time tracking).
    rounding_minutes: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    rounding_mode: Mapped[str] = mapped_column(
        String(10), nullable=False, default="up", server_default="up"
    )
    # Who the invoices are from (SPEC: invoicing). Shown on every invoice.
    business_name: Mapped[str | None] = mapped_column(String(200))
    business_address: Mapped[str | None] = mapped_column(Text)
    business_email: Mapped[str | None] = mapped_column(String(320))
    payment_instructions: Mapped[str | None] = mapped_column(Text)
    invoice_footer: Mapped[str | None] = mapped_column(Text)
    # A running timer older than this asks whether to trim or keep it.
    timer_warning_minutes: Mapped[int] = mapped_column(
        nullable=False, default=240, server_default="240"
    )


class Client(RecordMixin, Base):
    """Who you bill."""

    __tablename__ = "clients"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Short code used in invoice numbers like ACME-2026-001.
    code: Mapped[str | None] = mapped_column(String(20))
    contact_email: Mapped[str | None] = mapped_column(String(320))
    billing_address: Mapped[str | None] = mapped_column(Text)
    rate: Mapped[Decimal | None] = mapped_column(Rate)
    payment_terms_days: Mapped[int | None] = mapped_column()
    tax_rate: Mapped[Decimal | None] = mapped_column(Percent)
    notes: Mapped[str | None] = mapped_column(Text)

    workspace: Mapped[Workspace] = relationship()


class Project(RecordMixin, Base):
    """A body of work, or an idea (status "idea", usually no client)."""

    __tablename__ = "projects"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id"), index=True, nullable=False
    )
    client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clients.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active")
    rate: Mapped[Decimal | None] = mapped_column(Rate)
    budget_hours: Mapped[Decimal | None] = mapped_column(Hours)
    budget_amount: Mapped[Decimal | None] = mapped_column(Money)
    colour: Mapped[str] = mapped_column(String(7), nullable=False, default="#2f5d50")

    workspace: Mapped[Workspace] = relationship()
    client: Mapped[Client | None] = relationship()


class Activity(RecordMixin, Base):
    """The kind of work: audit, setup, support, travel."""

    __tablename__ = "activities"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    rate: Mapped[Decimal | None] = mapped_column(Rate)
    billable: Mapped[bool] = mapped_column(nullable=False, default=True)

    workspace: Mapped[Workspace] = relationship()
