"""Time entries (SPEC: data model, "Time entry")."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Index, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tahgul.models.base import Base, RecordMixin, UTCDateTime
from tahgul.models.work import Activity, Project, Workspace

ENTRY_SOURCES = ("timer", "manual", "timesheet", "calendar", "agent")


class TimeEntry(RecordMixin, Base):
    """A block of work. ``end`` is null while the timer is running."""

    __tablename__ = "time_entries"
    __table_args__ = (
        # One timer at a time across all workspaces (SPEC: "one timer at a time"):
        # every running row has the same value for this expression.
        Index(
            "uq_time_entries_one_running",
            text("(end_at IS NULL)"),
            unique=True,
            postgresql_where=text("end_at IS NULL AND deleted_at IS NULL"),
            sqlite_where=text("end_at IS NULL AND deleted_at IS NULL"),
        ),
        Index("ix_time_entries_start", "start_at"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id"), index=True, nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), index=True)
    activity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("activities.id"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    scope_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scopes.id"), index=True)
    # Set while the entry is on an invoice (draft or issued); invoiced time is locked.
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invoices.id"), index=True)
    invoice_line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invoice_lines.id"))

    start_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    end_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    duration_seconds: Mapped[int | None] = mapped_column()
    notes: Mapped[str | None] = mapped_column(Text)
    billable: Mapped[bool] = mapped_column(nullable=False, default=True)

    # An explicit rate for this entry, beating every other level.
    rate_override: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    # The rate resolved when the entry was saved, and where it came from. Frozen, so
    # changing a client's rate later never re-prices old work.
    rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    rate_source: Mapped[str | None] = mapped_column(String(10))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    source: Mapped[str] = mapped_column(String(10), nullable=False, default="manual")

    workspace: Mapped[Workspace] = relationship()
    project: Mapped[Project | None] = relationship()
    activity: Mapped[Activity | None] = relationship()
    task: Mapped["Task | None"] = relationship()  # noqa: F821 (string-resolved)
    scope: Mapped["Scope | None"] = relationship()  # noqa: F821

    @property
    def running(self) -> bool:
        return self.end_at is None
