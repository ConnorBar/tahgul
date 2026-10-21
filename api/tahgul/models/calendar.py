"""Calendar accounts, calendars and mirrored events (SPEC: calendar, two-way sync)."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tahgul.models.base import Base, RecordMixin, UTCDateTime
from tahgul.models.tasks import Task
from tahgul.models.time import TimeEntry
from tahgul.models.work import Project, Workspace


class CalendarAccount(RecordMixin, Base):
    """A CalDAV server login: the Radicale in the stack, or iCloud."""

    __tablename__ = "calendar_accounts"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="caldav")
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    username: Mapped[str] = mapped_column(String(320), nullable=False)
    # Encrypted with TAHGUL_SECRET_KEY; never stored or returned in plain text.
    password_enc: Mapped[str] = mapped_column(Text, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)

    calendars: Mapped[list["Calendar"]] = relationship(back_populates="account")


class Calendar(RecordMixin, Base):
    """One remote calendar. Read-only unless marked writable (SPEC: sync rules)."""

    __tablename__ = "calendars"
    __table_args__ = (UniqueConstraint("account_id", "url"),)

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calendar_accounts.id"), index=True, nullable=False
    )
    # Which workspace its events link into (projects, time entries).
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    colour: Mapped[str | None] = mapped_column(String(7))
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    # The server allows writes AND you chose to let Tahgul write here.
    server_writable: Mapped[bool] = mapped_column(nullable=False, default=False)
    writable: Mapped[bool] = mapped_column(nullable=False, default=False)
    # Where new events made in Tahgul (or by the agent) go.
    is_default: Mapped[bool] = mapped_column(nullable=False, default=False)
    # Time entries are mirrored here as events ("Billable" calendar).
    mirror_time_entries: Mapped[bool] = mapped_column(nullable=False, default=False)
    sync_token: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    account: Mapped[CalendarAccount] = relationship(back_populates="calendars")
    workspace: Mapped[Workspace] = relationship()


class CalendarEvent(RecordMixin, Base):
    """A mirror of one event (one iCalendar object) on a linked calendar."""

    __tablename__ = "calendar_events"
    __table_args__ = (
        UniqueConstraint("calendar_id", "href"),
        Index("ix_calendar_events_range", "calendar_id", "start_at", "end_at"),
    )

    calendar_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calendars.id"), index=True, nullable=False
    )
    href: Mapped[str] = mapped_column(String(1000), nullable=False)
    uid: Mapped[str] = mapped_column(String(500), nullable=False)
    etag: Mapped[str | None] = mapped_column(String(200))
    # The whole iCalendar object, so properties Tahgul doesn't edit survive a write.
    ics: Mapped[str] = mapped_column(Text, nullable=False)

    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(500))
    start_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    end_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    all_day: Mapped[bool] = mapped_column(nullable=False, default=False)
    recurring: Mapped[bool] = mapped_column(nullable=False, default=False)
    # Last time it changed anywhere (remote LAST-MODIFIED or our edit); newest wins.
    modified_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    # Changed in Tahgul but not yet accepted by the server.
    dirty: Mapped[bool] = mapped_column(nullable=False, default=False)
    pending_delete: Mapped[bool] = mapped_column(nullable=False, default=False)

    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    time_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("time_entries.id"), index=True
    )
    # "Log this as time?" was answered with no.
    log_dismissed: Mapped[bool] = mapped_column(nullable=False, default=False)
    # Why Tahgul made it: "manual" | "task_block" | "time_mirror" | None (made elsewhere)
    origin: Mapped[str | None] = mapped_column(String(20))

    calendar: Mapped[Calendar] = relationship()
    project: Mapped[Project | None] = relationship()
    task: Mapped[Task | None] = relationship()
    time_entry: Mapped[TimeEntry | None] = relationship(foreign_keys=[time_entry_id])
