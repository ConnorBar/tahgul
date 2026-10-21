"""Tasks, board columns, links, comments and saved views (SPEC: kanban and issue tracking)."""

import uuid
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import JSON, Date, Float, ForeignKey, Index, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tahgul.models.base import Base, RecordMixin, UTCDateTime
from tahgul.models.work import Project

TASK_TYPES = ("task", "bug", "idea", "request")
PRIORITIES = ("none", "low", "medium", "high", "urgent")
DEFAULT_COLUMNS = (("To do", False), ("In progress", False), ("Done", True))


class BoardColumn(RecordMixin, Base):
    """A status a project's board uses. Columns marked done count tasks as finished."""

    __tablename__ = "board_columns"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    position: Mapped[int] = mapped_column(nullable=False, default=0)
    is_done: Mapped[bool] = mapped_column(nullable=False, default=False)

    project: Mapped[Project] = relationship()


class Task(RecordMixin, Base):
    """A unit of work on the board (an "issue")."""

    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("workspace_id", "number"),
        Index("ix_tasks_column_position", "column_id", "position"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id"), index=True, nullable=False
    )
    # Short per-workspace reference, e.g. #12, so "task 12" works by voice.
    number: Mapped[int] = mapped_column(nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id"), index=True, nullable=False
    )
    column_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("board_columns.id"), nullable=False)
    scope_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scopes.id"), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"), index=True)

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(10), nullable=False, default="task")
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="none")
    labels: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    due_date: Mapped[date | None] = mapped_column(Date, index=True)
    due_time: Mapped[time | None] = mapped_column(Time)
    estimate_minutes: Mapped[int | None] = mapped_column()
    # Order within the column; lower is higher on the board.
    position: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    project: Mapped[Project] = relationship()
    column: Mapped[BoardColumn] = relationship()
    parent: Mapped["Task | None"] = relationship(remote_side="Task.id")


class TaskLink(RecordMixin, Base):
    """``blocker`` must be finished before ``task`` can be (blocks / blocked-by)."""

    __tablename__ = "task_links"
    __table_args__ = (UniqueConstraint("task_id", "blocker_id"),)

    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"), index=True, nullable=False)
    blocker_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id"), index=True, nullable=False
    )


class Comment(RecordMixin, Base):
    """A comment on a task or project, by the owner, an agent token or the system."""

    __tablename__ = "comments"

    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), index=True)
    author_type: Mapped[str] = mapped_column(String(20), nullable=False)
    author_name: Mapped[str] = mapped_column(String(100), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)


class SavedView(RecordMixin, Base):
    """A named set of task filters, e.g. "Due this week, all clients"."""

    __tablename__ = "saved_views"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    filters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    position: Mapped[int] = mapped_column(nullable=False, default=0)
