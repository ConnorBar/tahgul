"""Scopes and invoices (SPEC: "Scopes and budgets", "Invoicing")."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tahgul.models.base import Base, RecordMixin, UTCDateTime
from tahgul.models.work import Client, Project, Workspace

SCOPE_TYPES = ("fixed", "hourly", "retainer")
SCOPE_STATUSES = ("draft", "agreed", "done")
INVOICE_STATUSES = ("draft", "sent", "paid", "void")
GROUPINGS = ("project", "activity", "scope", "day", "entry")


class Scope(RecordMixin, Base):
    """An agreed chunk of a project: a statement of work, a phase or a retainer."""

    __tablename__ = "scopes"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(10), nullable=False, default="hourly")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="agreed")
    # Hourly rate for work in this scope (beats project and client; SPEC rate order).
    rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    # Fixed fee for the whole scope, or the monthly retainer.
    fee: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    budget_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    budget_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # Retainers: hours included each month; more is billed hourly.
    included_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    # A fixed-fee scope is billed once; this is when.
    invoiced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    project: Mapped[Project] = relationship()


class Invoice(RecordMixin, Base):
    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("workspace_id", "number"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id"), index=True, nullable=False
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clients.id"), index=True, nullable=False
    )
    # Assigned when the invoice is issued, never before, so numbers have no gaps.
    number: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="draft")
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    group_by: Mapped[str] = mapped_column(String(10), nullable=False, default="project")
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text)
    # Frozen copies at issue time, so a later address change never alters a sent invoice.
    bill_from: Mapped[str | None] = mapped_column(Text)
    bill_to: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    paid_on: Mapped[date | None] = mapped_column(Date)
    voided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    pdf_path: Mapped[str | None] = mapped_column(String(500))

    workspace: Mapped[Workspace] = relationship()
    client: Mapped[Client] = relationship()
    lines: Mapped[list["InvoiceLine"]] = relationship(
        back_populates="invoice", order_by="InvoiceLine.position"
    )


class InvoiceLine(RecordMixin, Base):
    __tablename__ = "invoice_lines"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id"), index=True, nullable=False
    )
    position: Mapped[int] = mapped_column(nullable=False, default=0)
    # time | fixed | retainer | overage | manual
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(10), nullable=False, default="hours")
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    scope_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scopes.id"), index=True)
    # Retainer month this line bills, "2026-10".
    period: Mapped[str | None] = mapped_column(String(7))

    invoice: Mapped[Invoice] = relationship(back_populates="lines")


class InvoiceSequence(RecordMixin, Base):
    """The last number used per counter bucket (see tahgul.numbering.sequence_key)."""

    __tablename__ = "invoice_sequences"
    __table_args__ = (UniqueConstraint("workspace_id", "key"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    last_value: Mapped[int] = mapped_column(nullable=False, default=0)
