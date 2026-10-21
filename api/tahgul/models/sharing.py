"""Read-only report links for clients (SPEC: later, "a client-facing read-only report link")."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tahgul.models.base import Base, RecordMixin, UTCDateTime
from tahgul.models.work import Client, Project


class ReportLink(RecordMixin, Base):
    __tablename__ = "report_links"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id"), index=True, nullable=False
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clients.id"), index=True, nullable=False
    )
    # One project only, or (None) every project for the client.
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    # The secret part of the URL: looked up by hash, kept encrypted so it can be copied again.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    show_amounts: Mapped[bool] = mapped_column(nullable=False, default=False)
    show_tasks: Mapped[bool] = mapped_column(nullable=False, default=True)
    show_notes: Mapped[bool] = mapped_column(nullable=False, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_viewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    view_count: Mapped[int] = mapped_column(nullable=False, default=0)

    client: Mapped[Client] = relationship()
    project: Mapped[Project | None] = relationship()
